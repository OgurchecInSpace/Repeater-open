import requests
import json
import time
from pprint import pprint
import discord
import os
from config import settings
import threading
from datetime import datetime


# Структура хранения данных (data.json):
# {
# channel_id:
#     [group_id, ...]
# }

# groups_names.json:
# {group_id: group_name}


# Класс бота (потом как-нибудь)
class Repeater(discord.Client):
    def __init__(self, intents):
        super(Repeater, self).__init__(intents=intents)
        self.cooldown = 30  # В секундах
        self.embed_color = 0xffffff  # Белый
        self.api_acc_id = '51451568'
        self.jumoreski = -92876084
        self.test = -218675277
        self.settings = settings
        self.prefix = self.settings["prefix"]
        self.data = {}
        self.groups_names = {}
        self.ok_emoji = '✅'
        self.not_ok_emoji = '❌'

    # Функция для получения последнего поста сообщества. Использует requests
    def get_latest_post(self, group_id, get_photos=True):
        # Параметры запроса для vk api
        params = {
            'access_token': self.settings['access_token'], 'url': 'https://api.vk.com/method/wall.get',
            'count': 2,
            'client_id': self.api_acc_id, 'owner_id': group_id,
            'v': 5.131, 'extended': 1
        }
        post = requests.get(params['url'], params).content
        post = json.loads(post)['response']
        # От поста берутся только текст и фото, потому что я не знаю, как взять видео
        # Проверка на количество постов. Если постов 0, то, в некоторых случаях, дальнейшее действие останавливается.
        if len(post['items']) == 2:
            index = post['items'][0].get('is_pinned', 0)
        elif len(post['items']) == 1:
            index = 0
        else:
            return {'group_name': post['groups'][0]['name'],
                    'is_broken': True}

        answer = {'text': post['items'][index]['text'],  # Результат работы функции, выраженной словарём
                  'photos': {},
                  'date': post['items'][index]['date'],
                  'group_name': post['groups'][0]['name'],
                  'is_broken': False}
        if get_photos:
            # Проходимся по вложениям и ищем фотографии.
            for counter in range(len(post['items'][index]['attachments'])):
                # Если фотографии всё-таки есть, то сохраняем их.
                if isinstance(post['items'][index]['attachments'][counter], dict) \
                        and 'photo' in post['items'][index]['attachments'][counter].keys():
                    # Тут выбирается url фотографии самого лучшего разрешения
                    url_image = max(post['items'][index]['attachments'][counter]['photo']['sizes'],

                                    key=lambda inspect_image: inspect_image['height'] * inspect_image['width'])['url']
                    image = requests.get(url_image)  # Сама фотография, собственно
                    name_new_file = f'images\content_image_{counter}_{answer["group_name"]}.png'
                    with open(name_new_file, 'wb') as file:
                        file.write(image.content)
                        answer['photos'][name_new_file] = url_image

        return answer

    # Функция, выполняемая в отдельном потоке, которая каждые cooldown секунд отправляет запросы сообществам VK и
    # рассылает новости каналам, которые на них подписаны.
    def check_news(self):
        work_time = 1
        while True:
            start = time.mktime(datetime.today().timetuple())
            for channel in self.data:
                for group_id in self.data[channel]:
                    post_data = self.get_latest_post(group_id)
                    if not post_data['is_broken']:
                        # Если пост достаточно свеж (свежесть измеряется во времени, пост, чей возраст больше
                        # cooldown + work_time * 1.5 (для подстраховки), считается не свежим),
                        # то мы его публикуем
                        now_date = time.mktime(datetime.today().timetuple())  # Текущая дата
                        # print(f'Время, прошедшее с публикации поста - {now_date - post_data["date"]}')
                        # print(f'Имя группы - {post_data["group_name"]}')
                        # print(f'Допустимое время - {self.cooldown + int(work_time * 1.5)}')
                        if now_date - post_data['date'] < self.cooldown + int(work_time * 1.5):
                            # print('Публикуем')
                            self.dispatch('found_news', channel, post_data)
                        else:
                            # print('Не публикуем')
                            for photo in post_data['photos']:
                                os.remove(photo)
                        # print()
            end = time.mktime(datetime.today().timetuple())
            if end - start > 5:
                work_time = end - start
            else:
                work_time = 5

            time.sleep(self.cooldown)

    # Реакция на событие запуска бота.
    async def on_ready(self):
        # Открытие файла и запуск потока слежения за пабликами перенесены сюда, чтобы они начинались только тогда,
        # когда бот запустится.

        with open('data.json', 'rb') as start_data:
            load_data = json.load(start_data)
            self.data = {}
            for channel_id in load_data:
                load_channel = self.get_channel(int(channel_id))
                if load_channel is not None:
                    self.data[load_channel] = load_data[channel_id].copy()
        with open('groups_names.json', 'rb') as start_groups_names:
            self.groups_names = {int(key): value for key, value in json.load(start_groups_names).items()}

        threading.Thread(target=self.check_news, name='checking').start()
        print(f'Loaded as {self.user}')  # Загрузка
        await self.change_presence(status=discord.Status.online, activity=discord.Game("не играет"))

    # Реакция на сообщение в каком-либо канале.
    async def on_message(self, message):
        # Если команда состоит из 2-х слов и 2-ое - число, то:
        if len(message.content.split()) == 2 and message.content.lower().split()[1].isdigit():
            # Если пользователь - администратор, то выполняем команду
            if message.author.guild_permissions.administrator:
                vk_public_id = int(-int(message.content.lower().split()[1]))  # ID VK паблика

                # Добавление паблика в подписки
                if message.content.lower().startswith(f'{settings["prefix"]}добавить'):
                    # Если данный канал ещё никуда не подписывался, то создаём для него массив
                    if message.channel not in self.data:
                        self.data[message.channel] = []
                    # Если паблика ещё не добавляли, то добавляем
                    if vk_public_id not in self.data[message.channel]:
                        self.data[message.channel].append(vk_public_id)
                    await message.add_reaction(self.ok_emoji)

                # Удаление подписки на паблик
                elif message.content.lower().startswith(f'{self.settings["prefix"]}удалить'):  # Удаление подписки
                    # Если паблик был в подписках, то удаляем
                    if vk_public_id in self.data[message.channel]:
                        self.data[message.channel].remove(vk_public_id)
                        await message.add_reaction(self.ok_emoji)
                    # Иначе - уведомляем об этом
                    else:
                        await message.add_reaction(self.not_ok_emoji)
                        await message.channel.send(f"{message.author.mention},"
                                                   f" этот канал не подписан на данное сообщество!")
                self.save()
            # Иначе - уведомляем об этом.
            else:
                await message.add_reaction(self.not_ok_emoji)
                await message.channel.send(f'{message.author.mention}, Вы не являетесь администратором!')

        # Если же только 1, то проверяем, не %subscriptions ли это.
        elif message.content.lower().startswith(f'{self.settings["prefix"]}подписки'):
            subscriptions = discord.Embed(title="Подписки этого канала:", color=self.embed_color)
            names = []
            for group_id in self.data.get(message.channel, []):
                if group_id in self.groups_names:
                    names.append(self.groups_names[group_id])
                else:
                    name = self.get_latest_post(group_id, get_photos=False)['group_name']
                    names.append(name)
                    self.groups_names[group_id] = name

            subscriptions.set_footer(text='\n'.join(
                [f'{counter_id[0]}. {group_name} (ID={abs(counter_id[1])})' for counter_id, group_name in zip(
                    enumerate(self.data.get(message.channel, []), start=1),
                    names)]
            ))
            await message.channel.send(embed=subscriptions)
            with open('groups_names.json', 'w') as common_group_names:
                json.dump(self.groups_names, common_group_names)

        elif message.content.lower().startswith(f'{self.settings["prefix"]}помощь'):
            await self.help(message.channel)

    def help(self, channel):
        # Текст, который потом отправится
        text = ['Это Repeater-бот!',
                'Этот бот пересылает посты из VK сообществ.',
                'На данный момент, он НЕ пересылает видео (те, что из VK), а видео из YouTube, к сожалению, представляются простой ссылкой.',
                'Список команд:',
                f'{self.settings["prefix"]}добавить <id vk сообщества> - добавляет в подписки канала, из которого вызвали сообщение, переданное сообщество.',
                f'{self.settings["prefix"]}удалить <vk group id> - удаляет переданное сообщество из подписок канала.',
                f'{self.settings["prefix"]}подписки - список всех подписок канала, из которого вызывалась команда.',
                f'{self.settings["prefix"]}помощь - справка о боте и том, как им пользоваться.',
                'На данный момент бот находится в разработке, возможны баги и прочая муть.',
                'Разработчик: Jagorrim#6537, просьба писать ему о всех ошибках и недочётах',
                'Также возможны перебои в работе бота из-за отсутствия постоянного хоста.']
        text_embed = discord.Embed(title="Привет,", color=self.embed_color)
        text_embed.set_footer(text='\n'.join(text))
        return channel.send(embed=text_embed)

    # Реакция на событие присоединения к серверу
    async def on_guild_join(self, guild):
        # Тут мы выбираем ПЕРВЫЙ и ТЕКСТОВЫЙ канал из всех каналов на сервере
        channel = list(filter(lambda guild_channel:
                              isinstance(guild_channel, discord.guild.TextChannel), guild.channels))[0]
        await self.help(channel)

    # Реакция на кик с сервера
    async def on_guild_remove(self, guild):
        # Все текстовые каналы (откуда можно подписаться)
        channels = list(filter(lambda guild_channel:
                               isinstance(guild_channel, discord.guild.TextChannel), guild.channels))
        for channel in channels:
            if channel in self.data:
                del self.data[channel]
        self.save()

    # Сохранение
    def save(self):
        with open('data.json', 'w') as common_data:
            dumped_data = {}
            for channel in self.data:
                dumped_data[int(channel.id)] = self.data[channel].copy()
            json.dump(dumped_data, common_data)

    # Функция, которая отправляет сообщение с определённым содержимым в определённый канал. Реагирует на
    # события, которые создаются в check_news().
    async def on_found_news(self, channel, post_data):
        try:
            await channel.send(f"**Новый пост от: {post_data['group_name']}"
                               "\n\n\n"
                               f'{post_data["text"]}**',
                               files=list(map(discord.File, post_data['photos'])))
        except discord.errors.NotFound:
            del self.data[channel]
            self.save()
        for photo in post_data['photos']:
            os.remove(photo)


if __name__ == '__main__':
    api_acc_id = '51451568'
    jumoreski = -92876084
    test = -218675277
    bot_intents = discord.Intents.default()
    bot_intents.members = True
    bot_intents.presences = True
    client = Repeater(intents=bot_intents)
    client.run(settings['token'])

# В итоге храним в файлах json. Каналы в виде ID каналов, чтобы получить канал, надо:
# client.get_channel(id)
