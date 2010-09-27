# -*- coding: utf-8 -*-
"""
    tipfy.sessions
    ==============

    Lightweight sessions support for tipfy. Includes sessions using secure
    cookies and supports flash messages.

    :copyright: 2010 by tipfy.org.
    :license: Apache Sotware License, see LICENSE for details.
"""
import base64
import hashlib
import hmac
import logging
import time

from tipfy import APPENGINE, DEFAULT_VALUE, REQUIRED_VALUE
from tipfy.utils import json_encode, json_decode

from werkzeug import cached_property
from werkzeug.contrib.sessions import ModificationTrackingDict

#: Default configuration values for this module. Keys are:
#:
#: secret_key
#:     Secret key to generate session cookies. Set this to something random
#:     and unguessable. Default is :data:`tipfy.REQUIRED_VALUE` (an exception
#:     is raised if it is not set).
#:
#: default_backend
#:     The default backend to use when none is provided. Default is
#:     `securecookie`.
#:
#: cookie_name
#:     Name of the cookie to save a session or session id. Default is
#:     `session`.
#:
#: session_max_age:
#:     Default session expiration time in seconds. Limits the duration of the
#:     contents of a cookie, even if a session cookie exists. If None, the
#:     contents lasts as long as the cookie is valid. Default is None.
#:
#: cookie_args
#:     Default keyword arguments used to set a cookie. Keys are:
#:
#:     - max_age: Cookie max age in seconds. Limits the duration
#:       of a session cookie. If None, the cookie lasts until the client
#:       is closed. Default is None.
#:
#:     - domain: Domain of the cookie. To work accross subdomains the
#:       domain must be set to the main domain with a preceding dot, e.g.,
#:       cookies set for `.mydomain.org` will work in `foo.mydomain.org` and
#:       `bar.mydomain.org`. Default is None, which means that cookies will
#:       only work for the current subdomain.
#:
#:     - path: Path in which the authentication cookie is valid.
#:       Default is `/`.
#:
#:     - secure: Make the cookie only available via HTTPS.
#:
#:     - httponly: Disallow JavaScript to access the cookie.
default_config = {
    'secret_key':      REQUIRED_VALUE,
    'default_backend': 'securecookie',
    'cookie_name':     'session',
    'session_max_age': None,
    'cookie_args': {
        'max_age':     None,
        'domain':      None,
        'path':        '/',
        'secure':      None,
        'httponly':    False,
    }
}


class BaseSession(ModificationTrackingDict):
    def get_flashes(self, key='_flash'):
        """Returns a flash message. Flash messages are deleted when first read.

        :param key:
            Name of the flash key stored in the session. Default is '_flash'.
        :returns:
            The data stored in the flash, or an empty list.
        """
        if key not in self:
            # Avoid popping if the key doesn't exist to not modify the session.
            return []

        return self.pop(key, [])

    def add_flash(self, value, key='_flash'):
        """Sets a flash message. Flash messages are deleted when first read.

        :param value:
            Value to be saved in the flash message.
        :param key:
            Name of the flash key stored in the session. Default is '_flash'.
        """
        self.setdefault(key, []).append(value)


class SecureCookieSession(BaseSession):
    @classmethod
    def get_session(cls, store, name, **kwargs):
        return cls(store.get_secure_cookie(name) or ())

    def save_session(self, response, store, name, **kwargs):
        if not self.modified:
            return

        store.set_secure_cookie(response, name, dict(self), **kwargs)


class SecureCookieStore(object):
    """Encapsulates getting and setting secure cookies.

    Extracted from `Tornado`_ and modified.
    """
    def __init__(self, secret_key):
        """Initilizes this secure cookie store.

        :param secret_key:
            A long, random sequence of bytes to be used as the HMAC secret
            for the cookie signature.
        """
        self.secret_key = secret_key

    def get_cookie(self, request, name, max_age=None):
        """Returns the given signed cookie if it validates, or None.

        :param request:
            A :class:`tipfy.Request` object.
        :param name:
            Cookie name.
        :param max_age:
            Maximum age in seconds for a valid cookie. If the cookie is older
            than this, returns None.
        """
        value = request.cookies.get(name)

        if not value:
            return None

        parts = value.split('|')
        if len(parts) != 3:
            return None

        signature = self._get_signature(name, parts[0], parts[1])

        if not self._check_signature(parts[2], signature):
            logging.warning('Invalid cookie signature %r', value)
            return None

        if max_age is not None and (int(parts[1]) < time.time() - max_age):
            logging.warning('Expired cookie %r', value)
            return None

        try:
            return self._decode(parts[0])
        except:
            logging.warning('Cookie value failed to be decoded: %r', parts[0])
            return None

    def set_cookie(self, response, name, value, **kwargs):
        """Signs and timestamps a cookie so it cannot be forged.

        To read a cookie set with this method, use get_cookie().

        :param response:
            A :class:`tipfy.Response` instance.
        :param name:
            Cookie name.
        :param value:
            Cookie value.
        :param kwargs:
            Options to save the cookie. See :meth:`SessionStore.get_session`.
        """
        response.set_cookie(name, self.get_signed_value(name, value), **kwargs)

    def get_signed_value(self, name, value):
        """Returns a signed value for a cookie.

        :param name:
            Cookie name.
        :param value:
            Cookie value.
        :returns:
            An signed value using HMAC.
        """
        timestamp = str(int(time.time()))
        value = self._encode(value)
        signature = self._get_signature(name, value, timestamp)
        return '|'.join([value, timestamp, signature])

    def _encode(self, value):
        return base64.b64encode(json_encode(value, separators=(',', ':')))

    def _decode(self, value):
        return json_decode(base64.b64decode(value))

    def _get_signature(self, *parts):
        hash = hmac.new(self.secret_key, digestmod=hashlib.sha1)
        hash.update('|'.join(parts))
        return hash.hexdigest()

    def _check_signature(self, a, b):
        if len(a) != len(b):
            return False

        result = 0
        for x, y in zip(a, b):
            result |= ord(x) ^ ord(y)

        return result == 0


class SessionStore(object):
    #: A dictionary with the default supported backends.
    default_backends = {
        'securecookie': SecureCookieSession,
    }

    def __init__(self, app, request, backends=None):
        self.app = app
        self.request = request
        # Base configuration.
        self.config = app.get_config(__name__)
        # A dictionary of support backend classes.
        self.backends = backends or self.default_backends
        # The default backend to use when none is provided.
        self.default_backend = self.config.get('default_backend')
        # Tracked sessions.
        self._sessions = {}
        # Tracked cookies.
        self._cookies = {}

    @cached_property
    def secure_cookie_store(self):
        """Factory for secure cookies."""
        return SecureCookieStore(self.config.get('secret_key'))

    def get_cookie_args(self, **kwargs):
        """Returns a copy of the default cookie configuration updated with the
        passed arguments.

        :param kwargs:
            Keyword arguments to override in the cookie configuration.
        """
        _kwargs = self.config['cookie_args'].copy()
        _kwargs.update(kwargs)
        return _kwargs

    def get_session(self, key=None, backend=None, **kwargs):
        """Returns a session for a given key. If the session doesn't exist, a
        new session is returned.

        :param key:
            Cookie name. If not provided, uses the ``cookie_name``
            value configured for this module.
        :param backend:
            Name of the session backend to be used. If not set, uses the
            default backend.
        :param kwargs:
            Options to set the session cookie. Keys are the same that can be
            passed to ``Response.set_cookie``, and override the ``cookie_args``
            values configured for this module. If not set, use the configured
            values.
        :returns:
            A dictionary-like session object.
        """
        key = key or self.config['cookie_name']
        backend = backend or self.default_backend
        sessions = self._sessions.setdefault(backend, {})

        if key not in sessions:
            kwargs = self.get_cookie_args(**kwargs)
            value = self.backends[backend].get_session(self, key, **kwargs)
            self._sessions[backend][key] = (value, kwargs)

        return self._sessions[backend][key][0]

    def set_session(self, key, value, backend=None, **kwargs):
        """Sets a session value.

        :param key:
            Cookie name. See :meth:`get_session`.
        :param value:
            Session value.
        :param backend:
            Name of the session backend. See :meth:`get_session`.
        :param kwargs:
            Options to save the cookie. See :meth:`get_session`.
        """
        backend = backend or self.default_backend
        kwargs = self.get_cookie_args(**kwargs)
        self._sessions[backend][key] = (value, kwargs)

    def get_secure_cookie(self, name, max_age=DEFAULT_VALUE):
        """Returns a secure cookie from the request.

        :param name:
            Cookie name.
        :param max_age:
            Maximum age in seconds for a valid cookie. If the cookie is older
            than this, returns None.
        :returns:
            A secure cookie value or None if it is not set.
        """
        if max_age is DEFAULT_VALUE:
            max_age = self.config['session_max_age']

        return self.secure_cookie_store.get_cookie(self.request, name,
            max_age=max_age)

    def set_secure_cookie(self, response, name, value, **kwargs):
        """Sets a secure cookie in the response.

        :param response:
            A :class:`tipfy.Response` object.
        :param name:
            Cookie name.
        :param value:
            Cookie value. Must be a dictionary.
        :param kwargs:
            Options to save the cookie. See :meth:`get_session`.
        """
        assert isinstance(value, dict), 'Secure cookie values must be a dict.'
        kwargs = self.get_cookie_args(**kwargs)
        self.secure_cookie_store.set_cookie(response, name, value, **kwargs)

    def set_cookie(self, key, value, **kwargs):
        """Registers a cookie or secure cookie to be saved or deleted.

        :param key:
            Cookie name.
        :param value:
            Cookie value.
        :param kwargs:
            Options to save the cookie. See :meth:`get_session`.
        """
        self._cookies[key] = (value, self.get_cookie_args(**kwargs))

    def unset_cookie(self, key):
        """Unsets a cookie previously set. This won't delete the cookie, it
        just won't be saved.

        :param key:
            Cookie name.
        """
        self._cookies.pop(key, None)

    def delete_cookie(self, key, **kwargs):
        """Registers a cookie or secure cookie to be deleted.

        :param key:
            Cookie name.
        :param kwargs:
            Options to delete the cookie. See :meth:`get_session`.
        """
        self._cookies[key] = (None, self.get_cookie_args(**kwargs))

    def save(self, response):
        """Saves all cookies and sessions to a response object.

        :param response:
            A ``tipfy.Response`` object.
        """
        if self._cookies:
            for key, (value, kwargs) in self._cookies.iteritems():
                if value is None:
                    response.delete_cookie(key, path=kwargs.get('path', '/'),
                        domain=kwargs.get('domain', None))
                else:
                    response.set_cookie(key, value, **kwargs)

        if self._sessions:
            for sessions in self._sessions.values():
                for key, (value, kwargs) in sessions.iteritems():
                    value.save_session(response, self, key, **kwargs)

    @classmethod
    def factory(cls, _app, _name, **kwargs):
        if _name not in _app.request.registry:
            _app.request.registry[_name] = cls(_app, _app.request, **kwargs)

        return _app.request.registry[_name]


class SessionMiddleware(object):
    """Saves sessions at the end of a request."""
    def after_dispatch(self, handler, response):
        handler.request.session_store.save(response)
        return response


class BaseSessionMixin(object):
    """Base class for all session-related mixins. Must be used with
    :class:`SessionMiddleware`.
    """
    @cached_property
    def session_store(self):
        return self.request.session_store

    @cached_property
    def session(self):
        """A dictionary-like object that is persisted at the end of the
        request.
        """
        return self.request.session_store.get_session()


class CookieMixin(BaseSessionMixin):
    """Adds set_cookie() and delete_cookie() methods to a
    :class:`tipfy.RequestHandler`.
    """
    def set_cookie(self, key, value, **kwargs):
        self.session_store.set_cookie(key, value, **kwargs)

    def delete_cookie(self, key, **kwargs):
        self.session_store.delete_cookie(key, **kwargs)


class SecureCookieMixin(BaseSessionMixin):
    """A mixin that adds a get_secure_cookie() method to a
    :class:`tipfy.RequestHandler`.
    """
    def get_secure_cookie(self, key, **kwargs):
        """Returns a tracked secure cookie. See
        :meth:`SessionStore.get_secure_cookie`.
        """
        return self.session_store.get_session(key, backend='securecookie',
            **kwargs)


class FlashMixin(BaseSessionMixin):
    """A mixin that adds get_flash() and set_flash() methods to a
    :class:`tipfy.RequestHandler`.
    """
    def get_flashes(self, key='_flash'):
        """Returns a flash message. See :meth:`SessionStore.get_flash`."""
        return self.session.get_flashes(key)

    def add_flash(self, data, key='_flash'):
        """Sets a flash message. See :meth:`SessionStore.set_flash`."""
        self.session.add_flash(data, key)


class MessagesMixin(BaseSessionMixin):
    """A :class:`tipfy.RequestHandler` mixin for system messages."""
    @cached_property
    def messages(self):
        """A list of status messages to be displayed to the user."""
        messages = []
        messages.extend(self.session.get_flashes(key='_messages'))
        return messages

    def set_message(self, level, body, title=None, life=None, flash=False):
        """Adds a status message.

        :param level:
            Message level. Common values are "success", "error", "info" or
            "alert".
        :param body:
            Message contents.
        :param title:
            Optional message title.
        :param life:
            Message life time in seconds. User interface can implement
            a mechanism to make the message disappear after the elapsed time.
            If not set, the message is permanent.
        :returns:
            None.
        """
        message = {'level': level, 'title': title, 'body': body, 'life': life}
        if flash is True:
            self.session.add_flash(message, key='_messages')
        else:
            self.messages.append(message)


class SessionMixin(BaseSessionMixin):
    """A :class:`tipfy.RequestHandler` that provides access to the current
    session.
    """
    def get_session(self, key=None, backend=None, **kwargs):
        """Returns a session. See :meth:`SessionStore.get_session`."""
        return self.session_store.get_session(key, backend, **kwargs)

    def set_session(self, key, value, backend=None, **kwargs):
        return self.session_store.set_session(key, value, backend, **kwargs)


class AllSessionMixins(CookieMixin, SecureCookieMixin, FlashMixin,
    MessagesMixin, SessionMixin):
    """All session mixins combined in one."""


if APPENGINE:
    from tipfy.sessions.appengine import DatastoreSession, MemcacheSession

    SessionStore.default_backends.update({
        'datastore': DatastoreSession,
        'memcache':  MemcacheSession,
    })