# -*- coding: utf-8 -*-
import json
import logging
import uuid
from datetime import datetime as dt
from datetime import timedelta
from os.path import join, abspath, dirname

from flask import Flask, jsonify, make_response, request, Response, render_template
from flask_cors import CORS
from waitress import serve
from werkzeug.exceptions import default_exceptions
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import WSGIRequestHandler

from .. import __version__
from ..exts.hooks import hook_logging
from ..migrations.models import ConversationOfficial, PromptInfo
from ..openai.api import API


class ChatBot:
    __default_ip = '127.0.0.1'
    __default_port = 8008

    def __init__(self, chatgpt, debug=False, sentry=False):
        self.chatgpt = chatgpt
        self.debug = debug
        self.sentry = sentry
        self.log_level = logging.DEBUG if debug else logging.WARN

        hook_logging(level=self.log_level, format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
        self.logger = logging.getLogger('waitress')

    def run(self, bind_str, threads=8):
        host, port = self.__parse_bind(bind_str)

        resource_path = abspath(join(dirname(__file__), '..', 'flask'))
        app = Flask(__name__, static_url_path='',
                    static_folder=join(resource_path, 'static'),
                    template_folder=join(resource_path, 'templates'))
        app.wsgi_app = ProxyFix(app.wsgi_app, x_port=1)
        app.after_request(self.__after_request)

        CORS(app, resources={r'/api/*': {'supports_credentials': True, 'expose_headers': [
            'Content-Type',
            'Authorization',
            'X-Requested-With',
            'Accept',
            'Origin',
            'Access-Control-Request-Method',
            'Access-Control-Request-Headers',
            'Content-Disposition',
        ], 'max_age': 600}})

        for ex in default_exceptions:
            app.register_error_handler(ex, self.__handle_error)

        app.route('/api/models')(self.list_models)
        app.route('/api/conversations')(self.list_conversations)
        app.route('/api/conversations', methods=['DELETE'])(self.clear_conversations)
        app.route('/api/conversation/<conversation_id>')(self.get_conversation)
        app.route('/api/conversation/<conversation_id>', methods=['DELETE'])(self.del_conversation)
        app.route('/api/conversation/<conversation_id>', methods=['PATCH'])(self.set_conversation_title)
        app.route('/api/conversation/gen_title/<conversation_id>', methods=['POST'])(self.gen_conversation_title)
        app.route('/api/conversation/talk', methods=['POST'])(self.talk)
        app.route('/api/conversation/regenerate', methods=['POST'])(self.regenerate)
        app.route('/api/conversation/goon', methods=['POST'])(self.goon)

        app.route('/api/auth/session')(self.session)
        app.route('/api/accounts/check')(self.check)
        app.route('/_next/data/olf4sv64FWIcQ_zCGl90t/chat.json')(self.chat_info)

        app.route('/')(self.chat)
        app.route('/chat')(self.chat)
        app.route('/chat/<conversation_id>')(self.chat)

        if not self.debug:
            self.logger.warning('Serving on http://{}:{}'.format(host, port))

        WSGIRequestHandler.protocol_version = 'HTTP/1.1'
        serve(app, host=host, port=port, ident=None, threads=threads)

    @staticmethod
    def __after_request(resp):
        resp.headers['X-Server'] = 'pandora/{}'.format(__version__)

        return resp

    def __parse_bind(self, bind_str):
        sections = bind_str.split(':', 2)
        if len(sections) < 2:
            try:
                port = int(sections[0])
                return self.__default_ip, port
            except ValueError:
                return sections[0], self.__default_port

        return sections[0], int(sections[1])

    def __handle_error(self, e):
        self.logger.error(e)

        return make_response(jsonify({
            'code': e.code,
            'message': str(e.original_exception if self.debug and hasattr(e, 'original_exception') else e.name)
        }), 500)

    @staticmethod
    def __set_cookie(resp, token_key, max_age):
        resp.set_cookie('token-key', token_key, max_age=max_age, path='/', domain=None, httponly=True, samesite='Lax')

    @staticmethod
    def __get_token_key():
        return request.headers.get('X-Use-Token', request.cookies.get('token-key'))

    def chat(self, conversation_id=None):
        query = {'chatId': [conversation_id]} if conversation_id else {}

        token_key = request.args.get('token')
        rendered = render_template('chat.html', pandora_base=request.url_root.strip('/'), query=query)
        resp = make_response(rendered)

        if token_key:
            self.__set_cookie(resp, token_key, timedelta(days=30))

        sid = request.cookies.get('seperated_id')
        if not sid:
            resp.set_cookie('seperated_id', "{}".format(uuid.uuid4()), max_age=timedelta(days=30), path='/',
                            domain=None, httponly=True, samesite='Lax')

        return resp

    @staticmethod
    def session():
        ret = {
            'user': {
                'id': 'user-000000000000000000000000',
                'name': 'admin@openai.com',
                'email': 'admin@openai.com',
                'image': None,
                'picture': None,
                'groups': []
            },
            'expires': '2089-08-08T23:59:59.999Z',
            'accessToken': 'secret',
        }

        return jsonify(ret)

    @staticmethod
    def chat_info():
        ret = {
            'pageProps': {
                'user': {
                    'id': 'user-000000000000000000000000',
                    'name': 'admin@openai.com',
                    'email': 'admin@openai.com',
                    'image': None,
                    'picture': None,
                    'groups': []
                },
                'serviceStatus': {},
                'userCountry': 'US',
                'geoOk': True,
                'serviceAnnouncement': {
                    'paid': {},
                    'public': {}
                },
                'isUserInCanPayGroup': True
            },
            '__N_SSP': True
        }

        return jsonify(ret)

    @staticmethod
    def check():
        ret = {
            'account_plan': {
                'is_paid_subscription_active': True,
                'subscription_plan': 'chatgptplusplan',
                'account_user_role': 'account-owner',
                'was_paid_customer': True,
                'has_customer_object': True,
                'subscription_expires_at_timestamp': 3774355199
            },
            'user_country': 'US',
            'features': [
                'model_switcher',
                'dfw_message_feedback',
                'dfw_inline_message_regen_comparison',
                'model_preview',
                'system_message',
                'can_continue',
            ],
        }

        return jsonify(ret)

    def list_models(self):
        return self.__proxy_result(self.chatgpt.list_models(True, self.__get_token_key()))

    def list_conversations(self):
        offset = request.args.get('offset', '0')
        limit = request.args.get('limit', '20')

        sid = request.cookies.get('seperated_id')
        if sid:
            conversation_list = ConversationOfficial.wrap_conversation_list(offset, limit, sid)
            for conversation in conversation_list['items']:
                if conversation['title'] == 'New chat':
                    resp = self.get_conversation(conversation['id'])
                    conversation['title'] = resp.json['title']
                    ConversationOfficial.new_conversation(conversation['id'], conversation['title'], sid)
            return jsonify(conversation_list)
        return self.__proxy_result(self.chatgpt.list_conversations(offset, limit, True, self.__get_token_key()))

    def get_conversation(self, conversation_id):
        return self.__proxy_result(self.chatgpt.get_conversation(conversation_id, True, self.__get_token_key()))

    def del_conversation(self, conversation_id):
        ConversationOfficial.delete(conversation_id)
        return self.__proxy_result(self.chatgpt.del_conversation(conversation_id, True, self.__get_token_key()))

    def clear_conversations(self):
        sid = request.cookies.get("seperated_id")
        if sid:
            ConversationOfficial.clear(sid)
        return self.__proxy_result(self.chatgpt.clear_conversations(True, self.__get_token_key()))

    def set_conversation_title(self, conversation_id):
        title = request.json['title']

        return self.__proxy_result(
            self.chatgpt.set_conversation_title(conversation_id, title, True, self.__get_token_key()))

    def gen_conversation_title(self, conversation_id):
        payload = request.json
        model = payload['model']
        message_id = payload['message_id']

        ret = self.chatgpt.gen_conversation_title(conversation_id, model, message_id, True, self.__get_token_key())
        load_obj = json.loads(ret.text)
        if 'title' in load_obj:
            ConversationOfficial.new_conversation(conversation_id, title=load_obj['title'])
            # conv = ConversationOfficial.get(conversation_id)
            # if conv:
            # conv.title = load_obj['title']
            # conv.save()

        return self.__proxy_result(ret)

    def talk(self):
        payload = request.json
        prompt = payload['prompt']
        model = payload['model']
        message_id = payload['message_id']
        parent_message_id = payload['parent_message_id']
        conversation_id = payload.get('conversation_id')
        stream = payload.get('stream', True)

        status, header, generator = self.chatgpt.talk(prompt, model, message_id, parent_message_id, conversation_id,
                                                      stream,
                                                      self.__get_token_key())
        sid = request.cookies.get('seperated_id')
        return self.__process_stream(status, header, generator, stream,
                                     lambda talk_json: self.save_talk(conversation_id,
                                                                      message_id, model,
                                                                      parent_message_id,
                                                                      prompt, talk_json, sid))

    def goon(self):
        payload = request.json
        model = payload['model']
        parent_message_id = payload['parent_message_id']
        conversation_id = payload.get('conversation_id')
        stream = payload.get('stream', True)

        # 追加prompt
        status, header, generator = self.chatgpt.goon(model, parent_message_id, conversation_id, stream,
                                                      self.__get_token_key())
        return self.__process_stream(status, header, generator, stream,
                                     lambda talk_json: self.save_prompt_info(conversation_id, talk_json))

    def regenerate(self):
        payload = request.json

        conversation_id = payload.get('conversation_id')
        if not conversation_id:
            return self.talk()

        prompt = payload['prompt']
        model = payload['model']
        message_id = payload['message_id']
        parent_message_id = payload['parent_message_id']
        stream = payload.get('stream', True)

        # 覆盖prompt
        status, header, generator = self.chatgpt.regenerate_reply(prompt, model, conversation_id, message_id,
                                                                  parent_message_id, stream,
                                                                  self.__get_token_key())
        return self.__process_stream(status, header, generator, stream,
                                     lambda talk_json: self.save_prompt_info(conversation_id, talk_json))

    @staticmethod
    def save_talk(conversation_id, message_id, model, parent_message_id, prompt, talk_json, sid):
        ConversationOfficial.new_conversation(conversation_id or talk_json['conversation_id'], sid=sid)
        user_prompt = PromptInfo()
        user_prompt.conversation_id = conversation_id or talk_json['conversation_id']
        user_prompt.prompt_id = message_id
        user_prompt.parent_id = parent_message_id
        user_prompt.role = 'user'
        user_prompt.model = model
        user_prompt.create_time = dt.now().timestamp()
        user_prompt.content = prompt
        user_prompt.new()
        ChatBot.save_prompt_info(conversation_id, talk_json)
        #  title

    @staticmethod
    def save_prompt_info(conversation_id, talk_json):
        assist_prompt = PromptInfo()
        assist_prompt.conversation_id = conversation_id or talk_json['conversation_id']
        assist_prompt.prompt_id = talk_json['message']['id']
        assist_prompt.parent_id = talk_json['message']['metadata']['parent_id']
        assist_prompt.model = talk_json['message']['metadata']['model_slug']
        assist_prompt.create_time = talk_json['message']['create_time']
        assist_prompt.role = talk_json['message']['author']['role']
        assist_prompt.content = talk_json['message']['content']['parts'][0]
        assist_prompt.new()

    @staticmethod
    def __process_stream(status, headers, generator, stream, db_func=None):
        if stream:
            return Response(API.wrap_stream_out(generator, status, db_func), mimetype=headers['Content-Type'],
                            status=status)

        last_json = None
        for json in generator:
            last_json = json

        return make_response(last_json, status)

    @staticmethod
    def __proxy_result(remote_resp):
        resp = make_response(remote_resp.text)
        resp.content_type = remote_resp.headers['Content-Type']
        resp.status_code = remote_resp.status_code

        return resp
