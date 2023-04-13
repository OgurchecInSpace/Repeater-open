import requests
import json
import time
from pprint import pprint
import discord
import os
from config import settings
import threading
from datetime import datetime
import pickle


# Структура хранения данных (data.pickle):
# {
# channel_id:
#     [subscription, ...]
# }


# Класс подписки на сообщество VK
class Subscription:
    params = ['пинг']

    def __init__(self, group_id, channel_id, group_name, ping='нет'):
        self.group_id = group_id
        self.channel_id = channel_id
        self.group_name = group_name
        self.ping = ping

    def __repr__(self):
        return f'Подписка канала {self.channel_id} на паблик {self.group_id} {self.group_name}, пинг={self.ping}'


# Класс бота
class Repeater(discord.Client):
    def __init__(self, intents, allowed_mentions):
        super(Repeater, self).__init__(intents=intents, allowed_mentions=allowed_mentions)

        self.min_cooldown = 20  # В секундах
        self.min_work_time = 2
        self.embed_color = 0xffffff  # Белый
        self.api_acc_id = '51451568'
        self.jumoreski = -92876084
        self.test = -218675277
        self.settings = settings
        self.prefix = self.settings["prefix"]
        self.data = {}
        self.ok_emoji = '✅'
        self.not_ok_emoji = '❌'
        self.length_limit = 2000
        # Адекватно кулдаун определяется в методе on_ready
        # (когда запускается непосредственно бот и выгружаются данные из файла)
        self.cooldown = 0

    # Получение url VK видео
    def get_video_url(self, owner_id, video_id):
        params = {
            'access_token': self.settings['access_token'], 'url': 'https://api.vk.com/method/video.get',
            'count': 1,
            'client_id': self.api_acc_id,
            'videos': f'{owner_id}_{video_id}',
            'v': 5.131
        }
        post = json.loads(requests.get(params['url'], params).content)
        try:
            url = post['response']['items'][0]['player']
        except IndexError:
            url = 'Видео не найдено'
        except KeyError:
            url = 'Сбой в API VK'
        return url

    # Функция для получения последнего поста сообщества. Использует requests
    def get_latest_post(self, group_id, get_photos=True, get_videos=True):
        # Параметры запроса для vk api
        params = {
            'access_token': self.settings['access_token'], 'url': 'https://api.vk.com/method/wall.get',
            'count': 2,
            'client_id': self.api_acc_id, 'owner_id': group_id,
            'v': 5.131, 'extended': 1
        }
        post = json.loads(requests.get(params['url'], params).content)
        # pprint(post)

        if 'response' in post:
            post = post['response']
        else:
            return {'is_broken': True, 'is_group': False}
        # От поста берутся только текст и фото, потому что я не знаю, как взять видео
        # Проверка на количество постов. Если постов 0, то, в некоторых случаях, дальнейшее действие останавливается.
        if len(post['items']) == 2:
            index = post['items'][0].get('is_pinned', 0)
        elif len(post['items']) == 1:
            index = 0
        else:
            return {'is_broken': True, 'is_group': False}
        answer = {'text': post['items'][index]['text'],  # Результат работы функции, выраженной словарём
                  'reposted_text': {},
                  'photos': {},
                  'videos': [],
                  'date': post['items'][index]['date'],
                  'group_name': '',
                  'is_broken': False,
                  'group_id': post['items'][0]['owner_id'],
                  'is_group': True}
        # Обыгрывание ситуации, когда в посте имеются гиперссылки
        if '[' in answer['text'] and ']' in answer['text'] and '|' in answer['text']:
            # Заменяем текст на такой же, но с распарсенными гиперссылками
            answer['text'] = self.parse_hyperlinks(answer['text'])

        if post['groups']:
            answer['group_name'] = list(filter(lambda item: int(item['id']) == abs(int(group_id)),
                                               post['groups']))[0]['name']
        else:
            answer['group_name'] = f"{post['profiles'][0]['first_name']} {post['profiles'][0]['last_name']}"

        if answer['group_id'] >= 0:
            return {'is_broken': False, 'is_group': False}

        media = post['items'][index]['attachments'].copy()  # Список со всеми вложениями поста
        # Если есть репосты, то сохраняем тексты и добавляем в список фотографий и видео фотографии и видео с репоста
        if 'copy_history' in post['items'][index]:
            len_copy = len(post['items'][index]['copy_history'])
            for repost_index in range(len_copy):
                answer['reposted_text'][len_copy - repost_index] = \
                    post['items'][index]['copy_history'][repost_index]['text']
                if 'attachments' in post['items'][index]['copy_history'][repost_index]:
                    media += post['items'][index]['copy_history'][repost_index]['attachments'].copy()

        # Проходимся по вложениям и ищем фотографии.
        for counter in range(len(media)):
            # Если фотографии всё-таки есть (и их надо брать), то сохраняем их.
            if get_photos and 'photo' in media[counter]:
                # Тут выбирается url фотографии самого лучшего разрешения
                url_image = max(media[counter]['photo']['sizes'],
                                key=lambda inspect_image: inspect_image['height'] * inspect_image['width'])['url']
                image = requests.get(url_image)  # Сама фотография, собственно
                name_new_file = f'images\content_image_{counter}_{answer["group_id"]}.png'
                with open(name_new_file, 'wb') as file:
                    file.write(image.content)
                    answer['photos'][name_new_file] = url_image
            # Если есть видео, то забираем url их проигрывателя
            if get_videos and 'video' in media[counter]:
                video = media[counter]['video']
                owner_id = video['owner_id']
                video_id = video['id']
                video_url = self.get_video_url(owner_id, video_id)
                answer['videos'].append(video_url)

        # pprint(post['items'][index])
        return answer

    # Расчёт паузы между запросами
    def get_cooldown(self):
        subscriptions = []
        for item in self.data:
            subscriptions.extend(self.data[item])
        return len(subscriptions) * self.min_cooldown

    # Метод для парсинга ссылка с гиперссылками
    @staticmethod
    def parse_hyperlinks(post_text):
        text_index = 0
        new_text = ''
        while text_index < len(post_text):
            # Если текущий символ - "[", а после него есть и "]", а между ними есть "|", то
            # пробуем вычленить оттуда ссылку и текст, который замещает её
            if '[' == post_text[text_index] and ']' in post_text[text_index:] and \
                    '|' in post_text[text_index: post_text[text_index:].find(']') + 1 + text_index]:
                link_place = post_text[
                             text_index: post_text[text_index:].find(']') + 1 + text_index
                             ]
                link = link_place[1:link_place.find('|')]
                # Если ссылка НЕ имеет при себе префикса в виде сетевого протокола и домена вк (а такое может быть),
                # то добавляем их, чтобы ссылка была настоящей
                if not link.startswith('https://vk.com/'):
                    link = 'https://vk.com/' + link
                text = link_place[link_place.find('|') + 1: -1]

                new_text += f'{link} {text}'
                text_index += len(link_place)
            else:
                new_text += post_text[text_index]
                text_index += 1
        # Заменяем текст на такой же, но с распарсенными гиперссылками
        return new_text

    # Реакция на запуск бота
    async def on_ready(self):
        # Открытие файла и запуск потока слежения за пабликами перенесены сюда, чтобы они начинались только тогда,
        # когда бот запустится.
        with open('data.pickle', 'rb') as file:
            load_data = pickle.load(file)
            self.data = {}
            for channel_id in load_data:
                load_channel = self.get_channel(int(channel_id))
                if load_channel is not None:
                    self.data[load_channel] = load_data[channel_id].copy()
        self.cooldown = self.get_cooldown()
        threading.Thread(target=self.check_news, name='checking').start()
        print(f'Loaded as {self.user}')  # Загрузка
        await self.change_presence(status=discord.Status.online, activity=discord.Game("не играет"))

    # Функция, выполняемая в отдельном потоке, которая каждые cooldown секунд отправляет запросы сообществам VK и
    # рассылает новости каналам, которые на них подписаны.
    def check_news(self):
        self.cooldown = self.get_cooldown()
        work_time = self.min_work_time
        while True:
            start = time.mktime(datetime.today().timetuple())
            for channel in {item: self.data[item].copy() for item in self.data}:  # Такая запись нужная для глубокого копирования
                for subscription in self.data[channel]:
                    post_data = self.get_latest_post(subscription.group_id)
                    if not post_data['is_broken']:
                        # Если пост достаточно свеж (свежесть измеряется во времени, пост, чей возраст меньше
                        # cooldown + work_time + 1 (1 для подстраховки), считается свежим),
                        # то мы его публикуем
                        now_date = time.mktime(datetime.today().timetuple())  # Текущая дата
                        if now_date - post_data['date'] < self.cooldown + work_time + 1:
                            self.dispatch('found_news', channel, post_data, subscription.ping, channel.guild)
                        else:
                            for photo in post_data['photos']:
                                os.remove(photo)
            end = time.mktime(datetime.today().timetuple())
            if end - start > self.min_work_time:
                work_time = end - start
            else:
                work_time = self.min_work_time
            time.sleep(self.cooldown)

    # Реакция на сообщение в каком-либо канале.
    async def on_message(self, message):
        if message.content.lower().startswith(f'{self.settings["prefix"]}добавить'):
            for act in self.add(message):
                await act

        # Удаление подписки на паблик
        elif message.content.lower().startswith(f'{self.settings["prefix"]}удалить'):
            for act in self.remove(message):
                await act

        # Получение подписок
        elif message.content.lower().startswith(f'{self.settings["prefix"]}подписки'):
            await self.get_subscriptions(message.channel)

        # Команда получения помощи
        elif message.content.lower().startswith(f'{self.settings["prefix"]}помощь'):
            await self.help(message.channel)

        # Настройка параметров подписки
        elif message.content.lower().startswith(f'{self.settings["prefix"]}настроить'):
            for act in self.set_settings(message):
                await act

    # Добавление подписки
    def add(self, message):
        if message.author.guild_permissions.administrator:
            if len(message.content.split()) >= 2 and message.content.lower().split()[1].isdigit():
                vk_public_id = -abs(int(message.content.lower().split()[1]))  # ID VK паблика
                # Если данный канал ещё никуда не подписывался, то создаём для него массив
                if message.channel not in self.data:
                    self.data[message.channel] = []
                response = self.get_latest_post(vk_public_id, get_photos=False, get_videos=False)
                # Если паблика ещё не добавляли, паблик - группа и ответ vk api не поломанный, то добавляем
                if vk_public_id not in list(map(lambda obj: obj.group_id, self.data[message.channel])) \
                        and not response['is_broken'] and response['is_group']:
                    # Если аргументы есть, то смотрим, что это за аргументы
                    if len(message.content.split()) >= 3:
                        key_arg = message.content.lower().split()[2]
                        # Если синтаксис передачи аргументов корректен ("параметр=аргумент"), то идём дальше
                        if '=' in key_arg and len(key_arg.split('=')) == 2:
                            key, arg = key_arg.split('=')
                            # Если такой параметр вообще есть
                            if key in Subscription.params:
                                if key == 'пинг':
                                    if arg in ['да', 'нет'] or arg.isdigit() and \
                                            message.channel.guild.get_role(int(arg)) is not None:
                                        ping_status = {'нет': 'нет', 'да': '@everyone'}.get(arg, f'<@&{arg}>')
                                        new_subscription = Subscription(
                                            vk_public_id, message.channel.id, response['group_name'],
                                            ping=ping_status)
                                        self.data[message.channel].append(new_subscription)
                                        yield message.add_reaction(self.ok_emoji)

                                    else:
                                        yield message.add_reaction(self.not_ok_emoji)
                                        yield message.channel.send(f'{message.author.mention}, '
                                                                   f'переданный аргумент не корректен!')
                            else:
                                yield message.add_reaction(self.not_ok_emoji)
                                yield message.channel.send(f'{message.author.mention}, такого параметра нет!')
                        else:
                            yield message.add_reaction(self.not_ok_emoji)
                            yield message.channel.send(f'{message.author.mention}, передана некорректная пара '
                                                       f'"параметр=аргумент"!')
                    else:
                        new_subscription = Subscription(vk_public_id, message.channel.id, response['group_name'])
                        self.data[message.channel].append(new_subscription)
                        yield message.add_reaction(self.ok_emoji)
                # Иначе - уведомляем об этом в частных случаях
                else:
                    # Да, тут может быть несколько вариантов ошибки одновременно.
                    # Но тут расставлены ошибки в приоритете их важности (по моему мнению),
                    # так что может быть и 2 ошибки в команде, но выведется всё равно только 1
                    yield message.add_reaction(self.not_ok_emoji)
                    if vk_public_id in list(map(lambda obj: obj.group_id, self.data[message.channel])):
                        yield message.channel.send(f"{message.author.mention}, этот канал уже подписан "
                                                   f"на этот паблик!")
                    elif not response['is_group']:
                        yield message.channel.send(f"{message.author.mention}, переданный ID не является "
                                                   f"ID сообщества VK!")
                    elif response['is_broken']:
                        yield message.channel.send(f"{message.author.mention}, сообщество не имеет постов!")
            else:
                yield message.add_reaction(self.not_ok_emoji)
                yield message.channel.send(f'{message.author.mention}, ID сообщества не передано или не '
                                           f'является числом')
        else:
            yield message.add_reaction(self.not_ok_emoji)
            yield message.channel.send(f'{message.author.mention}, Вы не являетесь администратором!')
        self.save()

    # Удаление подписки
    def remove(self, message):
        # Если автор сообщения обладает правами администратора на сервере
        if message.author.guild_permissions.administrator:
            if len(message.content.split()) >= 2 and message.content.lower().split()[1].isdigit():
                vk_public_id = -abs(int(message.content.lower().split()[1]))  # ID VK паблика
                # Если паблик есть в подписках, то удаляем
                if vk_public_id in list(map(lambda obj: obj.group_id, self.data[message.channel])):
                    self.data[message.channel] = list(filter(lambda obj: obj.group_id != vk_public_id,
                                                             self.data[message.channel]))
                    yield message.add_reaction(self.ok_emoji)
                # Иначе - уведомляем об этом
                else:
                    yield message.add_reaction(self.not_ok_emoji)
                    yield message.channel.send(f"{message.author.mention},"
                                               f" этот канал не подписан на данное сообщество!")
            else:
                yield message.add_reaction(self.not_ok_emoji)
                yield message.channel.send(f'{message.author.mention}, ID сообщества не передано или не '
                                           f'является числом')
        else:
            yield message.add_reaction(self.not_ok_emoji)
            yield message.channel.send(f'{message.author.mention}, Вы не являетесь администратором!')
        self.save()

    # Получение подписок
    def get_subscriptions(self, channel):
        subscriptions = discord.Embed(title="Подписки этого канала:", color=self.embed_color)
        text = []
        # Собираем в красивом виде предложения с данными о подписках
        for counter, subscription in enumerate(self.data.get(channel, []), start=1):
            ping_text = subscription.ping
            if ping_text not in ['нет', '@everyone']:
                # Если пинг не отсутствует и не предназначен для всех, значит, берём имя роли, которая пингуется
                ping_text = channel.guild.get_role(int(ping_text[3:-1]))
                # Если такая роль таки есть, значит, берём её имя
                if ping_text is not None:
                    ping_text = ping_text.name
                # Иначе - уведомляем об этом
                else:
                    ping_text = 'удалённая роль'
            line = f'{counter}. {subscription.group_name} (ID={abs(subscription.group_id)}) ' \
                   f'(пинг={ping_text})'
            text.append(line)
        subscriptions.set_footer(text='\n'.join(text))  # Задаём текст
        return channel.send(embed=subscriptions)

    # Гайд
    def help(self, channel):
        # Текст, который потом отправится
        text = ['Это Repeater-бот!',
                'Этот бот пересылает посты из VK сообществ.',
                'Но есть несколько нюансов:',
                '1) Видео из VK приходят в виде ссылок на них.',
                '2) Подписаться на приватные в той или иной степени группы нельзя, т.к. не позволяет API VK.',
                'Для получения ID VK сообщества можно использовать этот сайт - https://regvk.com/id/',
                'Список команд:\n',
                f'{self.settings["prefix"]}добавить <ID VK сообщества> <пинг=да/нет/ID роли, '
                f'которую надо пинговать при отправке нового поста> - |ТРЕБУЮТСЯ ПРАВА АДМИНИСТРАТОРА| '
                f'добавляет в подписки канала переданное сообщество. Параметр "пинг" '
                f'настраивает пинг при отправки постов в этот канал. "пинг=да" сделает так, что каждый пост будет '
                f'сопровождаться "@everyone" в конце, если же Вы укажете ID какой-то другой роли, '
                f'то пинговаться будет она.\n',
                f'{self.settings["prefix"]}удалить <ID VK сообщества> - |ТРЕБУЮТСЯ ПРАВА АДМИНИСТРАТОРА| '
                f'удаляет переданное сообщество из подписок канала.\n',
                f'{self.settings["prefix"]}подписки - список всех подписок канала, из которого вызывалась команда.\n',
                f'{self.settings["prefix"]}помощь - информация о боте и том, как им пользоваться.\n',
                f'{self.settings["prefix"]}настроить <ID VK сообщества> <параметр=аргумент> - '
                f'настройка параметров подписки на какой-либо паблик.\n',
                'На данный момент бот находится в разработке, возможны баги и прочая муть.',
                'Разработчик: Jagorrim#6537, просьба писать ему о всех ошибках и недочётах.',
                'Возможны перебои в работе бота из-за отсутствия постоянного хоста.']
        text_embed = discord.Embed(title="Привет,", color=self.embed_color)
        text_embed.set_footer(text='\n'.join(text))
        return channel.send(embed=text_embed)

    # Настройка параметров отправки
    def set_settings(self, message):
        # %настроить <id группы> <параметр=значение>
        # Так как функция настройки возвращает несколько значений (она генератор),
        # то мы проходимся по ним и ожидаем их
        args = message.content.split()
        if len(args) == 3:
            # Если переданный ID группы есть в списке id групп вк, на которые подписан этот канал
            if -int(args[1]) in map(lambda obj: obj.group_id, self.data.get(message.channel, []).copy()):
                key_arg = args[2]
                # Если синтаксис передачи аргументов корректен ("параметр=аргумент"), то идём дальше
                if '=' in key_arg and len(key_arg.split('=')) == 2:
                    key, arg = key_arg.split('=')
                    # Если такой параметр вообще есть
                    if key in Subscription.params:
                        if key == 'пинг':
                            old_subscription = list(filter(lambda obj: obj.group_id == -int(args[1]),
                                                           self.data[message.channel]))[0]

                            if arg in ['да', 'нет'] or \
                                    arg.isdigit() and message.channel.guild.get_role(int(arg)) is not None:
                                ping_status = {'нет': 'нет', 'да': '@everyone'}.get(arg, f'<@&{arg}>')
                                self.data[message.channel] = list(
                                    filter(lambda obj: obj.group_id != -int(args[1]), self.data[message.channel])) + \
                                                             [Subscription(old_subscription.group_id,
                                                                           old_subscription.channel_id,
                                                                           old_subscription.group_name,
                                                                           ping=ping_status)]
                                yield message.add_reaction(self.ok_emoji)
                            else:
                                yield message.add_reaction(self.not_ok_emoji)
                                yield message.channel.send(f'{message.author.mention}, '
                                                           f'переданный аргумент не корректен!')
                    else:
                        yield message.add_reaction(self.not_ok_emoji)
                        yield message.channel.send(f'{message.author.mention}, такого параметра нет!')
                else:
                    yield message.add_reaction(self.not_ok_emoji)
                    yield message.channel.send(f'{message.author.mention}, передан некорректная пара '
                                               f'"параметр=аргумент"!')
            else:
                yield message.add_reaction(self.not_ok_emoji)
                yield message.channel.send(f'{message.author.mention}, данный канал не подписан на это сообщество!')
        else:
            yield message.add_reaction(self.not_ok_emoji)
            yield message.channel.send(f'{message.author.mention}, неправильные аргументы команды!')
        self.save()

    # Реакция на событие присоединения к серверу
    async def on_guild_join(self, guild):
        # Если на сервере есть каналы и среди них есть текстовые каналы,
        # то отправляем в первый встречный канал информацию
        if len(guild.channels) > 0 and discord.TextChannel in map(type, guild.channels):
            channel = list(filter(lambda item: isinstance(item, discord.TextChannel), guild.channels))[0]
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

    # Функция, которая отправляет сообщение с определённым содержимым в определённый канал. Реагирует на
    # события, которые создаются в check_news().
    async def on_found_news(self, channel, post_data, ping, guild):
        try:
            title = f"Новый пост от: {post_data['group_name']}""\n\n\n"
            videos = '\n'.join(
                [f'Видео №{counter} -  {url}' for counter, url in enumerate(post_data['videos'], start=1)]
            )
            text = title + post_data['text']

            # Репосты
            if post_data['reposted_text']:
                for index in post_data['reposted_text']:
                    text += '\n\n' + f'Текст из репоста №{index}:' + '\n\n' + post_data['reposted_text'][index]

            # Ссылки на видео
            if videos:
                text += '\n\n' + videos

            # Пинги
            if ping != 'нет':
                # Если пинг не для всех, то он точно пингует определённую роль, => надо проверить, что она есть
                if ping != '@everyone' and guild.get_role(int(ping[3:-1])) is None:
                    ping = '@удалённая роль'
                text += '\n\n' + ping

            # Выводим текст, постепенно обрезая его на части (по 1996 символов + 4 "*" для стилизации,
            # т.к. лимит в 2000 символов)
            while True:
                # Если длина текста + 4 звёздочки больше лимита, то берём кусок текста, а не весь
                if len(text) + 4 > self.length_limit:
                    await channel.send("**" + text[0: self.length_limit - 4] + "**")
                    # Обрезаем текст
                    text = text[self.length_limit - 4:]
                else:
                    await channel.send("**" + text + "**",
                                       files=list(map(discord.File, post_data['photos'])))
                    break
        # Если такой канал не найден (был удалён, или у бота нет доступа туда), то удаляем его и сохраняемся
        except discord.errors.NotFound:
            del self.data[channel]
            self.save()
        # time.sleep(0.15)
        # /\ нужно, т.к. функция отправки сообщения - асинхронная, следовательно, когда мы удаляем изображения,
        # Она ещё может их использовать
        for photo in post_data['photos']:
            os.remove(photo)

    # Сохранение данных
    def save(self):
        with open('data.pickle', 'wb') as file:
            dumped_data = {}
            for channel in self.data:
                dumped_data[int(channel.id)] = self.data[channel].copy()
            pickle.dump(dumped_data, file)


if __name__ == '__main__':
    api_acc_id = '51451568'
    jumoreski = -92876084
    test = -218675277
    my_id = 455961630
    bot_intents = discord.Intents.default()
    bot_intents.members = True
    bot_intents.presences = True
    client = Repeater(intents=bot_intents, allowed_mentions=discord.AllowedMentions(everyone=True))
    # pprint(client.get_latest_post(test))
    client.run(settings['token'])

# В итоге храним self.data в файле pickle. Каналы в виде ID каналов, чтобы получить канал (при запуске бота), надо:
# self.get_channel(id)
