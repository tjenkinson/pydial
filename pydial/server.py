import SocketServer
import socket
import struct
import time
import platform
import random
import uuid
import threading

from .common import (SSDP_PORT, SSDP_ADDR, SSDP_NT)

UPNP_SEARCH = 'M-SEARCH * HTTP/1.1'
# If we get a M-SEARCH with no or invalid MX value, wait up
# to this many seconds before responding to prevent flooding
CACHE_DEFAULT = 1800
BACKOFF_DEFAULT = 10
PRODUCT = 'PyDial Server'
VERSION = '0.01'

SSDP_REPLY = 'HTTP/1.1 200 OK\r\n' + \
               'LOCATION: {ddd_url}\r\n' + \
               'CACHE-CONTROL: max-age={max_age}\r\n' + \
               'EXT:\r\n' + \
               'BOOTID.UPNP.ORG: 1\r\n' + \
               'SERVER: {os_name}/{os_version} UPnP/1.1 {product_name}/{product_version}\r\n' + \
               'ST: {}\r\n'.format(SSDP_NT) + \
               'DATE: {date}\r\n' + \
               'USN: {usn}\r\n' + '\r\n'

SSDP_ANNOUNCE = 'NOTIFY * HTTP/1.1\r\n' + \
               'HOST: {}:{}\r\n'.format(SSDP_ADDR,SSDP_PORT) + \
               'LOCATION: {ddd_url}\r\n' + \
               'CACHE-CONTROL: max-age={max_age}\r\n' + \
               'NT: {nt}\r\n' + \
               'NTS: ssdp:alive\r\n' + \
               'EXT:\r\n' + \
               'BOOTID.UPNP.ORG: 1\r\n' + \
               'SERVER: {os_name}/{os_version} UPnP/1.1 {product_name}/{product_version}\r\n' + \
               'USN: {usn}\r\n' + \
               'CONFIGID.UPNP.ORG: 1\r\n' + '\r\n'

SSDP_BYEBYE = 'NOTIFY * HTTP/1.1\r\n' + \
               'HOST: {}:{}\r\n'.format(SSDP_ADDR,SSDP_PORT) + \
               'NT: {nt}\r\n' + \
               'NTS: ssdp:byebye\r\n' + \
               'BOOTID.UPNP.ORG: 1\r\n' + \
               'USN: {usn}\r\n' + \
               'CONFIGID.UPNP.ORG: 1\r\n' + '\r\n'


class SSDPHandler(SocketServer.BaseRequestHandler):
     """
     RequestHandler object to deal with DIAL UPnP search requests.

     Note that per the SSD protocol, the server will sleep for up
     to the number of seconds specified in the MX value of the 
     search request- this may cause the system to not respond if
     you are not using the multi-thread or forking mixin.
     """
     def __init__(self, request, client_address, server):
          SocketServer.BaseRequestHandler.__init__(self, request, 
                         client_address, server)
          self.max_backoff = BACKOFF_DEFAULT

     def handle(self):
          """
          Reads data from the socket, checks for the correct
          search parameters and UPnP search target, and replies
          with the application URL that the server advertises.
          """
          data = self.request[0].strip().split('\r\n')
          if data[0] != UPNP_SEARCH:
               return
          else:
               dial_search = False
               for line in data[1:]:
                    field, val = line.split(':', 1)
                    if field.strip() == 'ST' and val.strip() == SSDP_NT:
                         dial_search = True
                    elif field.strip() == 'MX':
                         try:
                              self.max_backoff = int(val.strip())
                         except ValueError:
                              # Use default
                              pass
               if dial_search:
                    self._send_reply()

     def _send_reply(self):
          """Sends reply to SSDP search messages."""
          time.sleep(random.randint(0, self.max_backoff))
          _socket = self.request[1]
          timestamp = time.strftime("%A, %d %B %Y %H:%M:%S GMT", 
                    time.gmtime())
          reply_data = SSDP_REPLY.format(date=timestamp, **self.server.fields)

          sent = 0
          while sent < len(reply_data):
               sent += _socket.sendto(reply_data, self.client_address)

class SSDPServer(SocketServer.UDPServer):
     """
     Inherits from SocketServer.UDPServer to implement the SSDP
     portions of the DIAL protocol- listening for search requests
     on port 1900 for messages to the DIAL multicast group and 
     replying with information on the URL used to request app
     actions from the server.

     Parameters:
          -device_url: Absolute URL of the device being advertised.
          -host: host/IP address to listen on

     The following attributes are set by default, but should be
     changed if you want to use this class as the basis for a 
     more complete server:
     product_id - Name of the server/product. Defaults to PyDial Server.
     product_version - Product version. Defaults to whatever version
          number PyDial was given during the last release.
     os_id - Operating system name. Default: platform.system()
     os_version - Operating system version. Default: platform.release().
     cache_expire - Time (in seconds) before a reply/advertisement expires.
          Defaults to 1800.
     uuid - UUID. By default created from the NIC via uuid.uuid1()
     """
     def __init__(self, device_url, host=''):
          SocketServer.UDPServer.__init__(self, (host, SSDP_PORT), 
                    SSDPHandler, False)
          self.allow_reuse_address = True
          self.server_bind()
          mreq = struct.pack("=4sl", socket.inet_aton(SSDP_ADDR),
                                       socket.INADDR_ANY)
          self.socket.setsockopt(socket.IPPROTO_IP, 
                    socket.IP_ADD_MEMBERSHIP, mreq)
          self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
          self.fields = {
              "ddd_url"         : device_url,
              "product_name"    : PRODUCT,
              "product_version" : VERSION,
              "os_name"         : platform.system(),
              "os_version"      : platform.release(),
              "max_age"         : CACHE_DEFAULT,
              "usn"             : "uuid:"+str(uuid.uuid1()),
          }
          self.announcer = None

     def start(self):
         if not self.announcer:
              self.announcer = SSDPAnnouncerThread(self)
              self.announcer.start()

              self.serve_forever()          
          
     def stop(self):
         # stop the timer thread
         if self.announcer:
             self.announcer.stop()
             self.announcer = None


class SSDPAnnouncerThread(threading.Thread):
    
    def __init__(self, server, repeats=3, minSpacing=0.1, advertisments=["upnp:rootdevice", SSDP_NT]):
        super(SSDPAnnouncerThread,self).__init__()
        self.daemon=True
        self.stopping = threading.Event()
        self.stopping.clear()
        self.server = server
        self.repeats = repeats
        self.minSpacing = minSpacing
        self.advertisments = advertisments
        
    def run(self):
        while not self.stopping.is_set():
            self._sendAll(SSDP_ANNOUNCE,"Announce")
            self.stopping.wait(float(self.server.fields["max_age"]) * 0.75) # scale down slightly
        self._sendAll(SSDP_BYEBYE,"Byebye")
        
    def stop(self):
        if self.isAlive():
            self.stopping.set()
            print "waiting for announcer thread to stop"
            self.join()
            print "announcer thread stopped"
            self.stopping.clear()
            
    def _sendAll(self, template,mode):
        for i in range(0,self.repeats):
            for advertisment in self.advertisments:
                self._sendOne(template, advertisment,mode)
                time.sleep(self.minSpacing)
    
    def _sendOne(self, template, advertisment, mode):
        fields = self.server.fields.copy()
        fields["nt"] = advertisment
        fields["usn"] = fields["usn"] + "::" + advertisment
        msg = template.format(**fields)
        self.server.socket.sendto(msg, (SSDP_ADDR, SSDP_PORT))
        print "Broadcasted: "+mode+" ... "+fields["nt"]

class DialServer(object):
     def __init__(self):
          pass

     def add_app(self, app_id, app_path):
          pass
