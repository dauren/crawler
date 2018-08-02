import aiohttp
import asyncio
import logging

from .request import Request, SleepTask
from .response import Response
from .error import UnknownTaskType


__all__ = ('Crawler',)


logger = logging.getLogger('crawler.base')


class Crawler(object):
    def __init__(self, concurrency=10):
        self._loop = asyncio.get_event_loop()
        self._task_queue = asyncio.Queue(maxsize=concurrency * 2)
        self._concurrency = concurrency
        self._free_workers = asyncio.Semaphore(value=concurrency)
        self._workers = {}

    def run(self):
        self._loop.run_until_complete(self.main_loop())

    async def task_generator_processor(self):
        for task in self.task_generator():
            if isinstance(task, Request):
                await self._task_queue.put(task)
            elif isinstance(task, SleepTask):
                await asyncio.sleep(task.delay)
            else:
                raise UnknownTaskType('Unknown task got from task_generator: '
                                      '%s' % task)

    def add_task(self, task):
        # blocking!
        list(self._task_queue.put(task))

    async def perform_request(self, req):
        logging.debug('GET {}'.format(req.url))
        try:
            async with aiohttp.ClientSession() as session:
                io_res = await asyncio.wait_for(
                    session.request('get', req.url),
                    req.timeout
                )
        except Exception as ex:
            self.process_failed_request(req, ex)
        else:
            try:
                body = await io_res.text()
            except Exception as ex:
                self.process_failed_request(req, ex)
            else:
                res = Response(
                    body=body,
                    # TODO: use effective URL (in case of redirect)
                    url=req.url,
                )
                handler = self._handlers[req.tag]
                # Call handler with arguments: request, response
                # Handler result could be generator or simple function
                # If handler is simple function then it must return None
                hdl_result = handler(req, res)
                if hdl_result is not None:
                    for item in hdl_result:
                        assert isinstance(item, Request)
                        await self._task_queue.put(item)

    def process_failed_request(self, req, ex):
        logging.error('', exc_info=ex)

    def register_handlers(self):
        self._handlers = {}
        for key in dir(self):
            if key.startswith('handler_'):
                thing = getattr(self, key)
                if callable(thing):
                    handler_tag = key[8:]
                    self._handlers[handler_tag] = thing

    def request_completed_callback(self, worker):
        self._free_workers.release()
        del self._workers[id(worker)]

    async def worker_manager(self):
        while True:
            task = await self._task_queue.get()
            await self._free_workers.acquire()
            worker = self._loop.create_task(self.perform_request(task))
            worker.add_done_callback(self.request_completed_callback)
            self._workers[id(worker)] = worker

    async def main_loop(self):
        self.prepare()
        self.register_handlers()
        task_gen_future = self._loop.create_task(
            self.task_generator_processor())
        worker_man_future = self._loop.create_task(self.worker_manager())
        self._main_loop_enabled = True
        try:
            while self._main_loop_enabled:
                if task_gen_future.done():
                    if task_gen_future.exception():
                        raise task_gen_future.exception()
                    if (not len(self._workers) and
                            not self._task_queue.qsize()):
                        self._main_loop_enabled = False
                await asyncio.sleep(0.05)
        finally:
            worker_man_future.cancel()
        self.shutdown()

    def shutdown(self):
        logger.debug('Work done!')

    def prepare(self):
        pass

    def task_generator(self):
        if False:
            yield None
