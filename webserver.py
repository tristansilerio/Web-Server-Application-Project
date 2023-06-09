#!/usr/bin/env python3
# Author: K. Walsh <kwalsh@cs.holycross.edu>
# Date: 15 January 2015
# Updated by Tristan S. : 25-26 September 2022 - Project Checkpoint 
#           - made sure to understand the code and how it worked
#           - did the file checks 
#           - added the index.html of HC homepage 
#           - working on default/directory pages 
#           - working on dynamic hello 
#           - almost done with Part 1 and understanding it 
# Updated by Tristan S. : 7 October 2022 - Final Checkpoint
#           - finished part 1 and part 2 
#           - implemented keep alive and dynamic hello page 
#           - finished the implementation of web chat app 
#
# Collaboration Log added to README.md
#
# A simple web server from scratch in Python. Run it like this:
#   ./webserver.py
# or:
#   python3 ./webserver.py
# or (if plain `python` on your system is really python version 3.x):
#   python ./webserver.py
#
# By default, it will listen on port 8888. If you want to use a different port
# (e.g. port 12345), you can provide the port number on the command line:
#   ./webserver.py 12345
#
# By default, it will serve files from the "./web_files" directory. If you want it
# to serve files from a different directory (e.g. "~/Desktop/Stuff"), you can
# provide the directory on the command line after the port number:
#   ./webserver.py 8888 ./Desktop/Stuff
#
# Note: This code is not "pythonic" at all; there are much more concise ways to
# write this code by using various python features like dicts and string
# interpolation. We also avoid use of any modules except for the following very
# basic things:

import os             # for os.path.isfile()
import socket         # for socket stuff
import sys
import urllib.parse   # for urllib.parse.unquote()
import time           # for time.time()
import threading      # for threading.Thread()
import random         # for random numbers
import re             # for regex split()
import string         # for various string operations
from ensurepip import version
from unittest import skip            # for sys.argv


# Global configuration variables, with default values.
# These never change once the server has finished initializing.
# For server_host, using "127.0.0.1" will ensure that your server is only
# accessible to browsers on your own laptop, and not from attackers trying to
# access your laptop from over the internet. Using an ampty string "" for
# server_host would allow all connections, potentially even from outside
# attackers.
server_host = "radius.holycross.edu"
server_port = 8888
server_root = "./web_files"
dynamic_greeting = 0
chat_rooms =  ["Main Room", "Club Chat", "Homework Chat"]
chat_text = ["", "", ""]
v = 0
chat_v= 0
updates = threading.Condition()


# Get command-line parameters, if present
if len(sys.argv) > 1:
    server_port = int(sys.argv[1])
if len(sys.argv) > 2:
    server_root = sys.argv[2]
server_root = os.path.normpath(server_root + '/')


# Global variables to keep track of statistics, with initial values. These get
# updated by different connection handler threads. To avoid race conditions,
# these should only be accessed within a "with" block, like this:
#     with stats.lock:
#        stats.tot_time += x
#        if stats.max_time < x:
#            ...
class Statistics:
    def __init__(self):
        self.total_connections = 0
        self.active_connections = 0
        self.num_requests = 0
        self.num_errors = 0
        self.tot_time = 0 # total time spent handling requests
        self.max_time = 0 # max time spent handling a request
        self.avg_time = 0 # average time spent handling requests
        self.lock = threading.Condition()
stats = Statistics()


# Request objects are used to hold information associated with a single HTTP
# request from a client.
class Request:
    def __init__(self):
        self.method = ""  # GET, POST, PUT, etc. for this request
        self.path = ""    # url path for this request
        self.version = "" # http version for this request
        self.headers = [] # headers from client for this request
        self.length = 0   # length of the request body, if any
        self.body = None  # contents of the request body, if any


# Response objects are used to hold information associated with a single HTTP
# response that will be sent to a client. The code is required, and should be
# something like "200 OK" or "404 NOT FOUND". The mime_type and body are
# options. If present, the mime_type should be something like "text/plain" or
# "image/png", and the body should be a string or raw bytes object containing
# contents appropriate for that mime type.
class Response:
    def __init__(self, code, mime_type=None, body=None):
        self.code = code
        self.mime_type = mime_type
        self.body = body


# Connection objects are used to hold information associated with a single HTTP
# connection socket, like the socket itself, statistics, any leftover data from
# the client that hasn't yet been processed, etc.
class Connection:
    def __init__(self, c, addr):
        self.sock = c             # the socket connected to the client
        self.client_addr = addr   # address of the client
        self.leftover_data = b""  # data from client, not yet processed
        self.num_requests = 0     # number of requests from client handled so far

    # read_until_blank_line() returns data from the client up to (but not
    # including) the next blank line, i.e. "\r\n\r\n". The "\r\n\r\n" sequence
    # is discarded. Any leftovers after the blank line is saved for later. This
    # function returns None if an error is encountered.
    def read_until_blank_line(self):
        data = self.leftover_data
        try:
            while b"\r\n\r\n" not in data:
                # Read (up to) another 4KB of data from the client
                more_data = self.sock.recv(4096)
                if not more_data: # Connection has died?
                    self.leftover_data = data # save it all for later
                    return None
                data = data + more_data
            # The part we want is everything up to the first blank line.
            data, self.leftover_data = data.split(b"\r\n\r\n", 1)
            return data.decode()
        except:
            log("Error reading from client %s socket" % (self.client_addr))
            self.leftover_data = data # save it all for later
            return None

    # read_amount(n) returns the next n bytes of data from the client. Any
    # leftovers after the n bytes are saved for later. This function returns
    # None if an error is encountered.
    def read_amount(self, n):
        data = self.leftover_data
        try:
            while len(data) < n:
                more_data = self.sock.recv(n - len(data))
                if not more_data: # Connection has died?
                    self.leftover_data = data # save it all for later
                    return None
                data = data + more_data
            # The part we want is the first n bytes.
            data, self.leftover_data = (data[0:n], data[n:])
            return data.decode()
        except:
            log("Error reading from client %s socket" % (self.client_addr))
            self.leftover_data = data # save it all for later
            return None


# log(msg) prints a message to standard output. Since multi-threading can jumble
# up the order of output on the screen, we print out the current thread's name
# on each line of output along with the message.
# Example usage:
#   log("Hello %s, you are customer number %d, have a nice day!" % (name, n))
def log(msg):
    # Convert msg to a string, if it is not already
    if not isinstance(msg, str):
        msg = str(msg)
    # Each python thread has a name. Use current thread's in the output message.
    myname = threading.current_thread().name
    # When printing multiple lines, indent each line a bit
    indent = (" " * len(myname))
    linebreak = "\n" + indent + ": "
    lines = msg.splitlines()
    msg = linebreak.join(lines)
    # Print it all out, prefixed by this thread's name.
    print(myname + ": " + msg)


# get_header_value() finds a specific header value from within a list of header
# key-value pairs. If the requested key is not found, None is returned instead.
# The headers list comes from an HTTP request sent from the client. The key
# should usually be a standard HTTP header, like "Content-Type",
# "Content-Length", "Connection", etc.
def get_header_value(headers, key):
    for hdr in headers:
        if hdr.lower().startswith(key.lower() + ": "):
            val = hdr.split(" ", 1)[1]
            return val
    return None

# make_printable() does some conversions on a string so that it prints nicely
# on the console while still showing unprintable characters (like "\r") in 
# a sensible way.
printable = string.ascii_letters + string.digits + string.punctuation + " \r\n\t"
def make_printable(s):
    s = s.replace("\n", "\\n\n")
    s = s.replace("\t", "\\t")
    s = s.replace("\r", "\\r")
    s = s.replace("\r", "\\r")
    return ''.join(c if c in printable else r'\x{0:02x}'.format(ord(c)) for c in s)

# handle_one_http_request() reads one HTTP request from the client, parses it,
# decides what to do with it, then sends an appropriate response back to the
# client. 
def handle_one_http_request(conn):

    # The HTTP request is everything up to the first blank line
    data = conn.read_until_blank_line()
    if data == None:
        return # something is wrong, maybe connection was closed by client?

    log("Request %d has arrived...\n%s" % (conn.num_requests, make_printable(data+"\r\n\r\n")))

    # Make a Request object to hold all the info about this request
    req = Request()

    # The first line is the request-line, the rest is the headers.
    lines = data.splitlines()
    if len(lines) == 0:
        log("Request is missing the required HTTP request-line")
        resp = Response("400 BAD REQUEST", "text/plain", "You need a request-line!")
        send_http_response(conn, resp)
        return
    request_line = lines[0]
    req.headers = lines[1:]

    # The request-line can be further split into method, path, and version.
    words = request_line.split()
    if len(words) != 3:
        log("The request-line is malformed: '%s'" % (request_line))
        resp = Response("400 BAD REQUEST", "text/plain", "Your request-line is malformed!")
        send_http_response(conn, resp)
        return
    req.method = words[0]
    req.path = words[1]
    req.version = words[2]

    log("Request has method=%s, path=%s, version=%s, and %d headers" % (
        req.method, req.path, req.version, len(req.headers)))

    # The path will look like either "/foo/bar" or "/foo/bar?key=val&baz=boo..."
    # Unmangle any '%'-signs in the path, but just the part before any '?'-mark
    if "?" in req.path:
        req.path, params = req.path.split("?", 1)
        req.path = urllib.parse.unquote(req.path) + "?" + params
    else:
        req.path = urllib.parse.unquote(req.path)

    # Browsers that use chunked transfer encoding are tricky, don't bother.
    if get_header_value(req.headers, "Transfer-Encoding") == "chunked":
        log("The request uses chunked transfer encoding, which isn't yet supported")
        resp = Response("411 LENGTH REQUIRED", "text/plain", "Your request uses chunked transfer encoding, sorry!")
        send_http_response(conn, resp)
        return

    if get_header_value(req.headers, "Connection") == "keep-alive": # keep alive check 
        conn.keepalive = True
    else:
        conn.keepalive = False
    # If request has a Content-Length header, get the body of the request.
    n = get_header_value(req.headers, "Content-Length")
    if n is not None:
        req.length = int(n)
        req.body = conn.read_amount(int(n))

    # Finally, look at the method and path to decide what to do.
    if req.method == "GET":
        resp = handle_http_get(req)
    elif req.method == "POST":
        resp = handle_http_post(req)
    else:
        log("HTTP method '%s' is not recognized or not yet implemented" % (req.method))
        resp = Response("405 METHOD NOT ALLOWED",
                "text/plain",
                "Unrecognized method: " + req.method)

    # Now send the response to the client.
    send_http_response(conn, resp)
    return conn.keepalive

# send_http_response() sends an HTTP response to the client. The response code
# should be something like "200 OK" or "404 NOT FOUND". The mime_type and body
# are sent as the contents of the response.
def send_http_response(conn, resp):
    # If this is anything other than code 200, tally it as an error.
    if not resp.code.startswith("200 "):
        with stats.lock: # update overall server statistics
            stats.num_errors += 1
    # Make a response-line and all the necessary headers.
    data = "HTTP/1.1 " + resp.code + "\r\n"
    data += "Server: csci356fa22\r\n"
    data += "Date: " + time.strftime("%a, %d %b %Y %H:%M:%S %Z") + "\r\n"

    body = None
    if resp.mime_type == None:
        data += "Content-Length: 0\r\n"
    else:
        if isinstance(resp.body, bytes):   # if response body is raw binary...
            body = resp.body               # ... no need to encode it
        elif isinstance(resp.body, str):   # if response body is a string...
            body = resp.body.encode()      # ... convert to raw binary
        else:                              # if response body is anything else...
            body = str(resp.body).encode() # ... convert it to raw binary
        data += "Content-Type: " + resp.mime_type + "\r\n"
        data += "Content-Length: " + str(len(body)) + "\r\n"
        if conn.keepalive == True:
            data += "Connection: keep-alive\r\n"
        elif conn.keepalive == False:
            data += "Connection: close\r\n"
    data += "\r\n"

    # Send response-line, headers, and body
    log("Sending response-line and headers...\n%s" % (make_printable(data)))
    conn.sock.sendall(data.encode())
    if body is not None:
        log("Response body (not shown) has %d bytes, mime type '%s'" % (len(body), resp.mime_type))
        conn.sock.sendall(body)


# handle_http_get_status() returns a response for GET /status
def handle_http_get_status():
    log("Handling http get status request")
    msg = "Web server for csci 356, version 0.02\n"
    msg += "\n"
    with stats.lock:
        msg += str(stats.total_connections) + " connections in total\n"
        msg += str(stats.active_connections) + " active connections\n"
        msg += str(stats.num_requests) + " requests handled\n"
        msg += str(stats.num_errors) + " errors encountered\n"
        msg += str(stats.avg_time) + "s average request handling time\n"
        msg += str(stats.max_time) + "s slowest request handling time\n"
    return Response("200 OK", "text/plain", msg)

# handle_http_get_hello() returns a response for GET /hello
def handle_http_get_hello():
    log("Handling http get hello request") # add track for access on this page
    msg = "<html>"
    msg = "<head><title>Hello, world!</title><head>\n"
    msg += "<body>"
    msg += "<p>Hit page refresh (F5) to refresh this page,\n"
    msg += "though the contents will never change, sadly.\n"
    msg += "\n"
    msg += '<p>You can also go to these exciting pages:\n'
    # Note: we use a full URL here, so user can copy-paste into the browser bar.
    # But if we were using HTML links, we could use a relative URL like
    # "/status" to avoid having to include the hostname and port number.
    msg += '<p>*  <a href ="/status">/status</a>                - status and statistics\n'
    msg += '<p>*  <a href ="/hello">/hello</a>                  - a standard greeting\n'
    #msg += '<p>*  <a href="http://localhost:8888/hello?username=Alice">  - a custom greeting\n'
    msg += '<p>*  <a href ="/quote">/quote</a>                  - a randomly generated quote\n'
    msg += '<p>*  <a href="/index.html">/index</a>          - Copy of Holy Cross Main Page\n'
    msg += '<p>*  <a href="/chat.html">/chat<a>            - a chat service\n'
    msg += '</body></html>'
    return Response("200 OK", "text/html", msg)

#Custom greeting given a username 
def handle_http_get_custom_greeting(user): 
    global dynamic_greeting 
    dynamic_greeting= dynamic_greeting + 1
    log("Handling http get custom greeting request")
    log("User %s"% user)
    msg = "<html>"
    msg = "<head><title>Welcome" + user + "!</title><head>\n"
    msg = "<head><title>Welcome!</title><head>\n"
    msg += "<body>"
    msg += "Hello " + user + "! Welcome!"
    msg += "<p>This page is a custom dynamic greeting, "
    msg += "though only some contents will change, sadly.\n"
    msg += "\n"
    msg += "<p>You can see that the background randomly changes color on refres and keeps a track of how many times "
    msg += "this page has been clicked."
    msg += "<p>Webpage visited " + str(dynamic_greeting) + " times in this web session."
    msg += '<p>You can also go to these exciting pages:\n'
    # Note: we use a full URL here, so user can copy-paste into the browser bar.
    # But if we were using HTML links, we could use a relative URL like
    # "/status" to avoid having to include the hostname and port number.
    msg += '<p>*  <a href ="/status">/status</a>                - status and statistics\n'
    msg += '<p>*  <a href ="/hello">/hello</a>                  - a standard greeting\n'
    #msg += '<p>*  <a href="http://localhost:8888/hello?username=Alice">  - a custom greeting\n'
    msg += '<p>*  <a href ="/quote">/quote</a>                  - a randomly generated quote\n'
    msg += '<p>*  <a href="/index.html">/index</a>          - Copy of Holy Cross Main Page\n'
    msg += '<p>*  <a href="/chat.html">/chat<a>            - a chat service\n'   
    msg += '</body></html>'
    return Response("200 OK", "text/html", msg)

# handle_http_get_quote() returns a response for the GET /quote
def handle_http_get_quote():
    log("Handling http get quote request")
    with open('quotations.txt') as f:
        quotes = re.split('(?m)^%$', f.read())
    msg = "<html><head><title>Quotes!</title></head>"
    msg += "<body>"
    msg += '<p>Here is a randomly generated quote from'
    msg += '  <a href="https://www.cs.cmu.edu/~pattis/quotations.html">Richard Pattis\' page</a> at CMU.'
    msg += "<pre>%s</pre>" % (random.choice(quotes))
    msg += '<p>Hit page refresh (F5) or <a href="/quote">click here</a> to refresh this page.</p>'
    msg += '<p>You can also check the <a href="/status">server status</a>, '
    msg += '  a <a href="/index.html">copy of the Holy Cross home page or something</a>, '
    msg += '  or a <a href="/chat.html">visit the chat rooms</a>.'
    msg += "</body></html>"
    return Response("200 OK", "text/html", msg)

# Handles http get chat version not 0
def handle_http_get_chat(): # version=0
    log("Handling http get chat version=0 request")
    msg = "0"
    msg += "\n"
    for i in range (0,len(chat_rooms)):
        msg += chat_rooms[i]
        msg += "\n"
    return Response("200 OK", "text/plain", msg)
# Handles http get chat versio not 0
def handle_http_get_otherChat(): #version!=0
    log("Handling http get chat version=not0 request")
    msg = str(v)
    msg += "\n"
    for i in range (0,len(chat_rooms)):
        msg += chat_rooms[i]
        msg += "\n"
    return Response("200 OK", "text/plain", msg)

# Handles posting the text to the chat room
def handle_http_get_chatText(text):  # display chat text
    log("Handling http get chat text request: " + text)
    msg = str(chat_v)
    msg += "\n"
    for i in range (0, len(chat_rooms)):
        if text in chat_rooms[i]:
            msg += chat_text[i]
            msg += "\n"
    return Response ("200 OK", "text/plain", msg)

# Handles creating a chat room
def handle_http_post_create(chatroom_name): # create chat room with a given name 
    chatroom_name = chatroom_name[1]
    check = False
    for i in range (0, len(chat_rooms)):
        if chatroom_name == chat_rooms[i]:
            check = True
    if check == False:
        chat_rooms.append(chatroom_name) #add new room to list of chat rooms
        chat_text.append("") # add blank text to new room
        with updates:
            global v
            v += 1
            updates.notify_all()
        msg = "Room Created. Success!"
        return Response("200 OK", "text/plain", msg)
    else:
        msg = "Sorry, Please try a different name. That name is taken."
        return Response("ERR", "text/plain", msg )

# Handles posting the chat in the chat room
def handle_http_post_chat(room, message): # posts the messages from the user
    with updates:
        global chat_v
        chat_v+= 1
        updates.notify_all()
    for i in range (0, len(chat_rooms)):
        if room in chat_rooms[i]:
            message = urllib.parse. unquote (message)
            chat_text[i] += " " + message + "\n"
    msg = "Message Sent. Success!"
    return Response("200 OK", "text/plain", msg)
        
# handle_http_get_file() returns an appropriate response for a GET request that
# seems to be for a file, rather than a special URL. If the file can't be found,
# or if there are any problems, an error response is generated.
def handle_http_get_file(url_path):
    log("Handling http get file request, for "+ url_path)
    file_path = server_root + url_path
    if file_path[-1] == '/':
        file_path += "index.html"
    # There is a very real security risk that the requested file_path could
    # include things like "..", allowing a malicious or curious client to access
    # files outside of the server's web_files directory. We take several
    # precautions here to make sure that there is no funny business going on.

    # First security precaution: "normalize" to eliminate ".." elements
    file_path = os.path.normpath(file_path)

    # Second security precaution: make sure the requested file is in server_root
    if os.path.commonprefix([file_path, server_root]) != server_root:
        log("Path traversal attack detected: " + url_path)
        return Response("403 FORBIDDEN", "text/plain", "Permission denied: " + url_path)

    # Third security precaution: check if the path is actually a file
    if not os.path.isfile(file_path):
        log("File was not found: " + file_path)
        return Response("404 NOT FOUND", "text/plain", "No such file: " + url_path)

    # Finally, attempt to read data from the file, and return it
    try:
        with open(file_path, "rb") as f: # "rb" mode means read "raw bytes"
            data = f.read()
        if file_path.lower().endswith('.png'):
            #mime_type = "image/png"
            return Response("200 OK", "image/png", data)
        elif file_path.lower().endswith('.jpg') or file_path.lower().endswith('.jpeg'):
            #mime_type = "image/jpeg"
            return Response("200 OK", "image/jpeg", data)
        elif file_path.lower().endswith('.htm') or file_path.lower().endswith('.html'):
            #log("MEOWMEOWMEOWMEOWMEOWMEOW")
            #mime_type = "text/html"
            return Response("200 OK", "text/html", data)
        elif file_path.lower().endswith('.txt'):
            #mime_type = "text/plain"
            return Response("200 OK", "text/plain", data)
        elif file_path.lower().endswith('.css'):
            #mime_type = "text/css"
            return Response("200 OK", "text/css", data)
        elif file_path.lower().endswith('.js'):
            #mime_type = "text/javascript"
            return Response("200 OK", "text/javascript", data)  
        else: 
            mime_type = "text/html" # for now, assume file contains html data
            return Response("200 OK", mime_type, data)
    except:
        log("Error encountered reading from file")
        return Response("403 FORBIDDEN", "text/plain", "Permission denied: " + url_path)
# handle_http_get() returns an appropriate response for a GET request
def handle_http_get(req):
    # Generate a response
    if req.path == "/status":
        resp = handle_http_get_status()
    elif req.path == "/hello":
        resp = handle_http_get_hello()
    elif req.path == "/quote":
        resp = handle_http_get_quote()
    elif req.path == "/":
        resp = handle_http_get_file("/index.html")
    elif "/hello?username=" in req.path: # check what user name they input
        userInput = req.path.partition('=')[2]
        resp = handle_http_get_custom_greeting(userInput)
    elif req.path == "/chat/roomlist?version=0":
       resp = handle_http_get_chat()
    elif "/chat/roomlist?version=" in req.path and req.path != "/chat/roomlist?version=0":
        with updates:
            number = req.path.partition('=')[2]
            while v < int(number):
                updates.wait()
        resp = handle_http_get_otherChat()
    elif "/chat/room/" in req.path:
        part = req.path.partition('?')[0]       # parition path to what we want to see
        chat = part.partition('/chat/room/')[2]
        with updates:
            number = req.path.partition('=')[2]
            while chat_v< int(number):
                updates.wait()
        resp = handle_http_get_chatText(chat)
    else:
        resp = handle_http_get_file(req.path)
    return resp

def handle_http_post(req):  # handle post 
    if req.path == "/chat/createroom":
        line = req.body.split("\n", 1)
        name = line[0].split("=", 1)
        resp = handle_http_post_create(name)
    elif "/chat/room/" in req.path:
        r = req.path.partition('?')[0]
        room = r.partition('/chat/room/')[2]
        message = req.path.partition('=')[2]
        resp = handle_http_post_chat(room, message)
    return resp

# handle_http_connection() reads one or more HTTP requests from a client, parses
# each one, and sends back appropriate responses to the client.
def handle_http_connection(conn):
        with stats.lock: # update overall server statistics
            stats.active_connections += 1
        log("Handling connection from " + str(conn.client_addr))
        try:
            # Process one HTTP request from client
            start = time.time()
            handle_one_http_request(conn)
            end = time.time()
            duration = end - start
            # Do end-of-request statistics and cleanup
            conn.num_requests += 1 # counter for this connection
            log("Done handling request %d from %s" % (conn.num_requests, conn.client_addr))
            with stats.lock: # update overall server statistics
                stats.num_requests += 1
                stats.tot_time = stats.tot_time + duration
                stats.avg_time = stats.tot_time / stats.num_requests
                if duration > stats.max_time:
                    stats.max_time = duration
            
            while conn.keepalive == True:
                start = time.time()
                handle_one_http_request(conn)
                end = time.time()
                duration = end - start
                conn.num_requests += 1 # update counter
                log("Done handling request %d from %s" % (conn.num_requests, conn.client_addr))
                with stats.lock: # update overall server statistics
                    stats.num_requests += 1
                    stats.tot_time = stats.tot_time + duration
                    stats.avg_time = stats.tot_time / stats.num_requests
                    if duration > stats.max_time:
                        stats.max_time = duration
        finally:
            conn.sock.close()
            log("Done with connection from " + str(conn.client_addr))
            with stats.lock: # update overall server statistics
                stats.active_connections -= 1


# This remainder of this file is the main program, which listens on a server
# socket for incoming connections from clients, and starts a handler thread for
# each one.

# Print a welcome message
server_addr = (server_host, server_port)
log("Starting web server")
log("Listening on address %s:%d" % (server_host, server_port))
log("Serving files from %s" % (server_root))
log("Ready for connections...")

# Create the server welcoming socket, and set it up to listen for connections
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(server_addr)
s.listen(5)

try:
    # Repeatedly accept and handle connections
    while True:
        sock, client_addr = s.accept()
        # A new client socket connection has been accepted. Count it.
        with stats.lock:
            stats.total_connections += 1
        # Put the info into a Connection object.
        conn = Connection(sock, client_addr)
        # Start a thread to handle the new connection.
        t = threading.Thread(target=handle_http_connection, args=(conn,))
        t.daemon = True
        t.start()
finally:
    log("Shutting down...")
    s.close()

log("Done")