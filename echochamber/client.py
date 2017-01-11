import os
import re
import logging
import StringIO

import pytest
import pexpect
import time


class Client(object):
    """
    Interface with a jabberite client which communicates using the
    np1sec protocol over XMPP
    """
    def __init__(self, xmpp_account, port=5222, config=None, proxy=None):
        self.xmpp_user, self.xmpp_password = xmpp_account["user"], xmpp_account["password"]
        self.username = self.xmpp_user.split("@")[0]
        self.port = str(port)
        self.proxy = proxy

        self._process = None
        self._process_output = StringIO.StringIO()
        self.conversation_id = None
        self.debug = False

        self.np1sec_path = config["np1sec_path"]
        self.ld_library_path = config["ld_library_path"]
        self.jabberite_bin = os.path.join(self.np1sec_path, "jabberite")

        self.messages = []
        self.r_message = re.compile(r"^\*\* <(\d+)> <(\w+)> (.*)$")

    def set_debug(self, enable=True):
        """Enable debug log for this client"""
        if enable:
            log_file = open("{}.log".format(self.username), "wb")
            self._process.logfile = log_file
        else:
            self._process.logfile = None

    def send_message(self, message):
        """Send a message to the jabberite client"""
        return self._process.sendline(message)

    def expect(self, pattern, *args, **kwargs):
        """Cleaner interface to expect on the jabberite pexpect process"""
        return self._process.expect(pattern, *args, **kwargs)

    def read_line(self, timeout):
        end = time.time() + timeout

        line = ""
        while True:
            b = self._process.read_nonblocking(1, max(0, end - time.time()))
            if b == "\n":
                return line
            line += b

    def read_event(self, timeout):
        line = self.read_line(timeout)

        if self.r_message.match(line):
            self.messages.append(line)

    def read_message(self, timeout):
        end = time.time() + timeout

        while True:
            if self.messages:
                return self.messages.pop(0)

            time_remaining = max(0, end - time.time())
            self.read_event(time_remaining)

    def connect(self, room, server="conference.localhost"):
        """Start the jabberite client process"""
        cmd_args = ["--account", self.xmpp_user,
                    "--password", self.xmpp_password,
                    "--server", server,
                    "--port", self.port,
                    "--room", room,
                    ]
        self.env = {"LD_LIBRARY_PATH": "{}:{}".format(os.path.join(self.np1sec_path),
                                                      self.ld_library_path)}

        # Run Jabberite with GDB if the flag is set
        if pytest.config.getoption("--run-with-gdb"):
            command = "gdb"
            cmd_args = ["-ex", "run", "--args", self.jabberite_bin] + cmd_args
        else:
            command = self.jabberite_bin

        if not self._process:
            # Start pexpect controlled jabberite client
            self._process = pexpect.spawn(
                command, cmd_args,
                env=self.env, echo=False, timeout=3600,
            )

            self._process.logfile = None
            self._process.logfile_read = self._process_output

            # For debugging full stdout can be echoed to screen.
            # self._process.logfile = sys.stdout

            # Wait for the client to finish connecting
            self.expect(r"\*\* Connected")
            logging.debug("Started client: %s", " ".join(cmd_args))

    def create_conversation(self):
        """Create np1sec conversation and return the new conversation ID"""
        self.send_message("/create")
        self.expect(r"\*\* Created conversation (\d+):")
        return int(self._process.match.group(1))

    def select_conversation(self, conversation_id):
        """Select an np1sec conversation"""
        self.send_message("/select {}".format(conversation_id))
        self.expect(r"\*\* Selecting conversation (\d+)")
        self.conversation_id = int(self._process.match.group(1))
        return self.conversation_id

    def invite_conversation(self, username):
        """Invite a user to the current conversation"""
        self.send_message("/invite {}".format(username))
        self.expect(r"\*\* <(\d)> {} invited user {}".format(self.username, username))
        logging.info("%s was invited to the conversation", username)

    def join_conversation(self, conversation_id):
        """
        Join an existing conversation which this client has been invited too
        """
        # Need to wait for the invitation process to complete before trying to join
        self.expect(r"\*\* Invited to conversation {}".format(conversation_id))

        self.select_conversation(conversation_id)
        self.send_message("/join")
        self.expect("you joined the chat session")
        logging.info("%s joined the chat session", self.username)

    def invite_and_join_conversation(self, client):
        """Invite a user and then join them to the current conversation"""
        self.invite_conversation(client.username)
        client.join_conversation(self.conversation_id)

        # We need to wait for the client and leader to be ready. The client state
        # is checked in join_conversation(). We wait for the leader to be ready
        # with the following expect().
        self.expect("{} joined the chat session".format(client.username))
        logging.info("%s joined conversation %d", client.username, self.conversation_id)

    def stop(self):
        if self._process:
            self._process.terminate(force=True)
            logging.debug("Stopping client %s during clean up", self.xmpp_user)

    def __repr__(self):
        return "<JabberiteClient {}>".format(self.xmpp_user)
