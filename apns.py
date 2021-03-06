# PyAPNs was developed by Simon Whitaker <simon@goosoftware.co.uk>
# Source available at https://github.com/simonwhitaker/PyAPNs
#
# PyAPNs is distributed under the terms of the MIT license.
#
# Copyright (c) 2011 Goo Software Ltd
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from binascii import a2b_hex, b2a_hex
from datetime import datetime
from socket import socket, AF_INET, SOCK_STREAM
from struct import pack, unpack
import sys
import ssl

try:
    from ssl import wrap_socket
except ImportError:
    from socket import ssl as wrap_socket

try:
    import json
except ImportError:
    import simplejson as json

MAX_PAYLOAD_LENGTH = 256

class APNs(object):
    """A class representing an Apple Push Notification service connection"""

    def __init__(self, use_sandbox=False, cert_file=None, key_file=None):
        """
        Set use_sandbox to True to use the sandbox (test) APNs servers.
        Default is False.
        """
        super(APNs, self).__init__()
        self.use_sandbox = use_sandbox
        self.cert_file = cert_file
        self.key_file = key_file
        self._feedback_connection = None
        self._gateway_connection = None

    @staticmethod
    def packed_uchar(num):
        """
        Returns an unsigned char in packed form
        """
        return pack('>B', num)

    @staticmethod
    def packed_ushort_big_endian(num):
        """
        Returns an unsigned short in packed big-endian (network) form
        """
        return pack('>H', num)

    @staticmethod
    def unpacked_ushort_big_endian(bytes):
        """
        Returns an unsigned short from a packed big-endian (network) byte
        array
        """
        return unpack('>H', bytes)[0]

    @staticmethod
    def packed_uint_big_endian(num):
        """
        Returns an unsigned int in packed big-endian (network) form
        """
        return pack('>I', num)

    @staticmethod
    def unpacked_uint_big_endian(bytes):
        """
        Returns an unsigned int from a packed big-endian (network) byte array
        """
        return unpack('>I', bytes)[0]

    @property
    def feedback_server(self):
        if not self._feedback_connection:
            self._feedback_connection = FeedbackConnection(
                use_sandbox = self.use_sandbox,
                cert_file = self.cert_file,
                key_file = self.key_file
            )
        return self._feedback_connection

    @property
    def gateway_server(self):
        if not self._gateway_connection:
            self._gateway_connection = GatewayConnection(
                use_sandbox = self.use_sandbox,
                cert_file = self.cert_file,
                key_file = self.key_file
            )
        return self._gateway_connection


class APNsConnection(object):
    """
    A generic connection class for communicating with the APNs
    """
    def __init__(self, cert_file=None, key_file=None):
        super(APNsConnection, self).__init__()
        self.cert_file = cert_file
        self.key_file = key_file
        self._socket = None
        self._ssl = None

    def __del__(self):
        self._disconnect();

    def _connect(self):
        # Establish an SSL connection
        self._socket = socket(AF_INET, SOCK_STREAM)
        self._socket.connect((self.server, self.port))
        self._ssl = wrap_socket(self._socket, self.key_file, self.cert_file)
        self._ssl.setblocking(False)
        
    def _disconnect(self):
        if self._socket:
            self._socket.close()

    def _connection(self):
        if not self._ssl:
            self._connect()
        return self._ssl

    def read(self, n=None):
        try:
            ret = self._connection().recv(n)
            if ret:
                self.close()
                
            return ret
        except ssl.SSLZeroReturnError:
            # SSL protocol alerted close. We have a nice shutdown here.
            self.close()
            return None
        except ssl.SSLWantReadError:
            # non-blocking mode and there is no recv data.
            # no need to reset connection
            return None

    def write(self, string):
        try:
            ret = self._connection().write(string)
            return ret
        except IOError:
            # BrokenPipeError will be caught here.
            # connection has been closed or failed
            self.close()
            return None
    
    def close(self):
        if self._socket is not None:
            if self._ssl is not None:
                try:
                    self._connection().close()
                except:
                    pass
                
            try:
                self._socket.close()
            except:
                pass
            
            self._socket = None
            self._ssl = None
    
    def is_closed(self):
        return self._socket is None


class PayloadAlert(object):
    def __init__(self, body, action_loc_key=None, loc_key=None,
                 loc_args=None, launch_image=None):
        super(PayloadAlert, self).__init__()
        self.body = body
        self.action_loc_key = action_loc_key
        self.loc_key = loc_key
        self.loc_args = loc_args
        self.launch_image = launch_image

    def dict(self):
        d = { 'body': self.body }
        if self.action_loc_key:
            d['action-loc-key'] = self.action_loc_key
        if self.loc_key:
            d['loc-key'] = self.loc_key
        if self.loc_args:
            d['loc-args'] = self.loc_args
        if self.launch_image:
            d['launch-image'] = self.launch_image
        return d

class PayloadTooLargeError(Exception):
    def __init__(self, payload_size):
        super(PayloadTooLargeError, self).__init__()
        self.payload_size = payload_size

class Payload(object):
    """A class representing an APNs message payload"""
    def __init__(self, alert=None, badge=None, sound=None, custom={}, content_available=False):
        super(Payload, self).__init__()
        self.alert = alert
        self.badge = badge
        self.sound = sound
        self.custom = custom
        self.content_available = content_available
        self._check_size()

    def dict(self):
        """Returns the payload as a regular Python dictionary"""
        d = {}
        if self.alert:
            # Alert can be either a string or a PayloadAlert
            # object
            if isinstance(self.alert, PayloadAlert):
                d['alert'] = self.alert.dict()
            else:
                d['alert'] = self.alert
        if self.sound:
            d['sound'] = self.sound
        if self.badge is not None:
            d['badge'] = int(self.badge)

        if self.content_available:
            d.update({'content-available': 1})

        d = { 'aps': d }
        d.update(self.custom)
        return d

    def json(self):
        return json.dumps(self.dict(), separators=(',',':'), ensure_ascii=False, sort_keys=True).encode('utf-8')

    def _check_size(self):
        payload_length = len(self.json())
        if payload_length > MAX_PAYLOAD_LENGTH:
            raise PayloadTooLargeError(payload_length)

    def __repr__(self):
        attrs = ("alert", "badge", "sound", "custom")
        args = ", ".join(["%s=%r" % (n, getattr(self, n)) for n in attrs])
        return "%s(%s)" % (self.__class__.__name__, args)

class Frame(object):
    """A class representing an APNs message frame for multiple sending"""
    def __init__(self):
        self.frame_data = bytearray()

    def add_item(self, token_hex, payload, identifier, expiry, priority):
        """Add a notification message to the frame"""
        token_bin = a2b_hex(token_hex)
        token_length_bin = APNs.packed_ushort_big_endian(len(token_bin))
        token_item = bytes('\1', 'utf-8') + token_length_bin + token_bin
        self.frame_data.extend(token_item)
        
        payload_json = payload.json()
        payload_length_bin = APNs.packed_ushort_big_endian(len(payload_json))
        payload_item = bytes('\2', 'utf-8') + payload_length_bin + payload_json
        self.frame_data.extend(payload_item)

        identifier_bin = APNs.packed_uint_big_endian(identifier)
        identifier_length_bin = \
                APNs.packed_ushort_big_endian(len(identifier_bin))
        identifier_item = bytes('\3', 'utf-8') + identifier_length_bin + identifier_bin
        self.frame_data.extend(identifier_item)

        expiry_bin = APNs.packed_uint_big_endian(expiry)
        expiry_length_bin = APNs.packed_ushort_big_endian(len(expiry_bin))
        expiry_item = bytes('\4', 'utf-8') + expiry_length_bin + expiry_bin
        self.frame_data.extend(expiry_item)

        priority_bin = APNs.packed_uchar(priority)
        priority_length_bin = APNs.packed_ushort_big_endian(len(priority_bin))
        priority_item = bytes('\5', 'utf-8') + priority_length_bin + priority_bin
        self.frame_data.extend(priority_item)
    
    def get_frame(self):
        """Get the frame buffer"""
        return (bytes('\2', 'utf-8') + APNs.packed_uint_big_endian(len(self.frame_data)) +
                self.frame_data)


class Message(object):
    def __init__(self, token=None, payload=None, identifier=None, expiry=None, priority=None):
        self.token = token
        self.payload = payload
        self.identifier = identifier
        self.expiry = expiry
        self.priority = priority
        
class FeedbackConnection(APNsConnection):
    """
    A class representing a connection to the APNs Feedback server
    """
    def __init__(self, use_sandbox=False, **kwargs):
        super(FeedbackConnection, self).__init__(**kwargs)
        self.server = (
            'feedback.push.apple.com',
            'feedback.sandbox.push.apple.com')[use_sandbox]
        self.port = 2196

    def _chunks(self):
        BUF_SIZE = 4096
        while 1:
            data = self.read(BUF_SIZE)
            yield data
            if not data:
                break

    def items(self):
        """
        A generator that yields (token_hex, fail_time) pairs retrieved from
        the APNs feedback server
        """
        buff = bytes()
        for chunk in self._chunks():
            buff += chunk

            # Quit if there's no more data to read
            if not buff:
                break

            # Sanity check: after a socket read we should always have at least
            # 6 bytes in the buffer
            if len(buff) < 6:
                break

            while len(buff) > 6:
                token_length = APNs.unpacked_ushort_big_endian(buff[4:6])
                bytes_to_read = 6 + token_length
                if len(buff) >= bytes_to_read:
                    fail_time_unix = APNs.unpacked_uint_big_endian(buff[0:4])
                    fail_time = datetime.utcfromtimestamp(fail_time_unix)
                    token = b2a_hex(buff[6:bytes_to_read]).decode()

                    yield (token, fail_time)

                    # Remove data for current token from buffer
                    buff = buff[bytes_to_read:]
                else:
                    # break out of inner while loop - i.e. go and fetch
                    # some more data and append to buffer
                    break

class GatewayConnection(APNsConnection):
    """
    A class that represents a connection to the APNs gateway server
    """
    def __init__(self, use_sandbox=False, **kwargs):
        super(GatewayConnection, self).__init__(**kwargs)
        self.server = (
            'gateway.push.apple.com',
            'gateway.sandbox.push.apple.com')[use_sandbox]
        self.port = 2195

    def _get_notification(self, token_hex, payload):
        """
        Takes a token as a hex string and a payload as a Python dict and sends
        the notification
        """
        token_bin = a2b_hex(token_hex)
        token_length_bin = APNs.packed_ushort_big_endian(len(token_bin))
        payload_json = payload.json()
        payload_length_bin = APNs.packed_ushort_big_endian(len(payload_json))

        zero_byte = '\0'
        if sys.version_info[0] != 2:
            zero_byte = bytes(zero_byte, 'utf-8')
        notification = (zero_byte + token_length_bin + token_bin
            + payload_length_bin + payload_json)

        return notification

    def send_notification(self, token_hex, payload):
        return self.write(self._get_notification(token_hex, payload))

    def send_notification_multiple(self, frame):
        return self.write(frame.get_frame())
    
    def send(self, message):
        token_hex = message.token
        payload = message.payload
        identifier = message.identifier
        expiry = message.expiry
        priority = message.priority
        
        frame = Frame()
        frame.add_item(token_hex, payload, identifier, expiry, priority)
        
        return self.write(frame.get_frame())

