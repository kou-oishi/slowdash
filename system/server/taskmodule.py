#! /usr/bin/env python3
# Created by Sanshiro Enomoto on 24 May 2024 #

import time, logging, traceback
import threading
from usermodule import UserModule


class TaskFunctionThread(threading.Thread):
    def __init__(self, func, kwargs):
        super().__init__()
        self.func = func
        self.kwargs = kwargs

    def run(self):
        self.func(**self.kwargs)


        
class TaskModule(UserModule):
    def __init__(self, module, name, params, start_thread):
        super().__init__(module, name, params, start_thread)
        self.command_thread = None
        self.exports = None
        self.channel_list = None
        self.command_history = []

        logging.info('user task module loaded')
        
        
    def __del__(self):
        if self.command_thread is not None:
            if self.command_thread.is_alive():
                #kill
                pass
            thread.join()
        super().__del__()
        

    def is_command_running(self):
        return self.command_thread is not None and self.command_thread.is_alive()


    def scan_channels(self):
        self.channel_list = []
        self.exports = {}
        
        func = self.get_func('export')
        if func is None:
            return
        
        try:
            exports = func()
        except Exception as e:
            logging.error('user module error: export(): %s' % str(e))
            logging.error(traceback.format_exc())
            return None
        if exports is None:
            return

        for name, node in exports:
            self.exports[name] = node
            value = node.get()
            if type(value) == dict:
                if 'table' in value:
                    self.channel_list.append({'name': name, 'type': 'table'})
                else:
                    self.channel_list.append({'name': name, 'type': 'tree'})
            else:
                self.channel_list.append({'name': name})

        return self.channel_list
    
                
    def get_channels(self):
        return self.scan_channels()

    
    def get_data(self, channel):
        if self.channel_list is None:
            self.scan_channels()
            
        if channel not in self.exports:
            return None

        value = self.exports[channel].get()
        if type(value) == dict:
            if 'tree' in value or 'table' in value:
                return value
            else:
                return { 'tree': value }
        else:
            return str(value)

    
    def process_command(self, params):
        if self.func_process_command:
            return super().process_command(params)

        function_name, kwargs, is_async = None, {}, True
        for key, value in params.items():
            if len(key) > 2 and key.endswith('()'):
                function_name = key[:-2]
                if function_name.startswith('await '):
                    is_async = False
                    function_name = function_name[5:].lstrip()
            else:
                kwargs[key] = value

        # task namespace
        if function_name is None or not function_name.startswith(self.name + '.'):
            return None
        function_name = function_name[len(self.name)+1:]

        # task is single-threaded, except for loop()
        if self.command_thread is not None:
            if self.command_thread.is_alive():
                return {'status': 'error', 'message': 'command already running'}
            else:
                self.command_thread.join()
        
        func = self.get_func(function_name)
        if func is None:
            return {'status': 'error', 'message': 'undefined function: %s' % function_name}
        
        cmd = '%s.%s(%s)' % (self.name, function_name, ','.join(['%s=%s'%(key,value) for key,value in kwargs.items()]))
        self.command_history.append((time.time(), cmd))
        
        if is_async:
            self.command_thread = TaskFunctionThread(func, kwargs)
            self.command_thread.start()
        else:
            try:
                func(**kwargs)
            except Exception as e:
                return {'status': 'error', 'message': str(e) }

        return True
