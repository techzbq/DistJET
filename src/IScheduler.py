import Queue
import json
import time

from BaseThread import BaseThread
import Policy
import Task
import logger
from MPI_Wrapper import Tags

log = logger.getLogger('TaskScheduler')

def MSG_wrapper(**kwd):
    return json.dumps(kwd)


class IScheduler(BaseThread):
    def __init__(self, master, appmgr):
        BaseThread.__init__(self, name=self.__class__.__name__)
        self.master = master
        self.appmgr = appmgr
        self.task_todo_Queue = Queue.Queue()
        self.completed_Queue = Queue.Queue()
        #self.task_unschedule_queue = Queue.Queue()
        self.policy = Policy.Policy()

    def initialize(self):
        pass

    def set_running_task(self, tid):
        self.appmgr.current_app().task_list[tid].status = Task.TaskStatus.PROCESSING

    def has_more_work(self):
        """
        Return ture if current app has more work( when the number of works of app is larger than sum of workers' capacities)
        :return: bool
        """
        pass

    def worker_initialize(self, w_entry):
        """
        called by Master when a worker agent successfully initialized the worker, (maybe check the init_output)
        when the method returns, the worker can be marked as ready
        :param w_entry:
        :return:
        """
        raise NotImplementedError

    def worker_fininalize(self, w_entry):
        """
        called by Master when a worker agent need finish all task and need to do finalization
        :param w_entry:
        :return:
        """
        raise NotImplementedError

    def worker_removed(self, w_entry):
        """
        called by Master when a worker has been removed from worker Regitry list (lost. terminated or other reason)
        :param w_entry:
        :return:
        """
        pass

    def task_failed(self,wid, tid):
        """
        called when tasks completed with failure
        :param task:
        :return:
        """
        raise NotImplementedError

    def task_completed(self, wid, tid, time_start, time_finish):
        """
        this method is called when task completed ok.
        """
        raise NotImplementedError

    def task_unschedule(self, tasks):
        """
        called when tasks have been unschedule. tasks that have not been started or that are not completed
        :param tasks:
        :return:
        """

    def req_more_task(self, wid):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError

class SimpleScheduler(IScheduler):
    #policy = Policy()
    def __init__(self, master, appmgr):
        IScheduler.__init__(self, master,appmgr)
        #self.completed_tasks_queue = Queue.Queue()
        self.processing = True
        self.current_app = self.appmgr.current_app()[1]
        self.scheduled_task_queue = {}                  # record tasks matches worker 'wid':[tids]
        for t in self.current_app.task_list:
            self.task_todo_Queue.put(self.current_app.task_list[t])

    def worker_removed(self, w_entry):
        q = self.scheduled_task_queue[w_entry.wid]
        if len(q) != 0:
            for t in q:
                self.task_todo_Queue.put_nowait(self.current_app.task_list[t])
                q.remove(t)
        if len(self.scheduled_task_queue[w_entry.wid]) == 0:
            self.scheduled_task_queue.pop(w_entry.wid)
            log.info('TaskScheduler: remove worker=%d', w_entry.wid)

    def has_more_work(self):
        return not self.task_todo_Queue.empty()

    def worker_initialize(self, w_entry):
        if self.current_app.app_init_boot:
            send_str = MSG_wrapper(appid = self.appmgr.current_app_id, app_ini_boot=self.current_app.app_init_boot, app_ini_data=self.current_app.app_init_data,
                               res_dir=self.current_app.res_dir)
            self.master.server.send_string(send_str, len(send_str), w_entry.w_uuid, Tags.APP_INI)
        else:           #if no init boot, send empty string
            send_str = MSG_wrapper(appid = self.appmgr.current_app_id, app_ini_boot="", app_ini_data="",res_dir="")
            self.master.server.send_string(send_str,len(send_str), w_entry.w_uuid, Tags.APP_INI)


    def worker_fininalize(self, w_entry):
        if self.current_app.app_fin_boot:
            send_str = MSG_wrapper(appid=self.appmgr.current_app_id, app_fin_boot=self.current_app.app_fin_boot,
                                       app_fin_data=self.current_app.app_fin_data,
                                       res_dir=self.current_app.res_dir)
        else:
            send_str = MSG_wrapper(appid=self.appmgr.current_app_id, app_fin_boot="", app_fin_data="", res_dir="")
        log.info("TaskScheduler: worker=%d ask for finalize, send finalize msg=%s", w_entry.wid, send_str)
        self.master.server.send_string(send_str, len(send_str), w_entry.w_uuid, Tags.APP_FIN)




    def task_failed(self,wid, tid):
        task = self.current_app.get_task_by_id(tid)
        if self.policy.REDO_IF_FAILED_TASKS and len(task.history) < self.policy.REDO_LIMITS:
            log.info('TaskScheduler: task=%d fail, waiting for reassign', tid)
            self.task_unschedule(task)
        else:
            log.info('TaskScheduler: task=%d fail, ignored', tid)
            task.fail()
            self.completed_Queue.put_nowait(task)
        try:
            self.scheduled_task_queue[wid].remove(tid)
        except:
            log.error('TaskScheduler: @task_fail, remove task=%d from worker[%d] queue ERROR', tid,wid)

    def task_completed(self, wid, tid, time_start, time_finish):
        task = self.current_app.get_task_by_id(tid)
        task.complete(time_start, time_finish)
        self.completed_Queue.put(task)
        try:
            self.scheduled_task_queue[wid].remove(tid)
        except:
            print('Scheduler: remove scheduled_task_queue error')
        log.info('TaskScheduler: task=%d complete, task start time=%s, task finish time=%s', tid, time.strftime("&H:%M:%S",time.localtime(time_start)), time.strftime("&H:%M:%S",time.localtime(time_finish)))

    def task_unschedule(self, tasks):
        for t in tasks:
            self.task_todo_Queue.put(t)

    def req_more_task(self, wid):
        w_entry = self.master.worker_registry.get(wid)
        tmp_task = self.task_todo_Queue.get()
        self.master.schedule(w_entry.w_uuid, [tmp_task])

    def run(self):
        """
        1. split application into tasks
        2. check initialize worker
        3. assign tasks
        :return:
        """
        log.info("TaskScheduler: start...")

        # initialize worker
        while self.current_app:
            task_num = 0
            self.scheduled_task_queue = {}
            self.processing = True
            # split task
            #TODO self.appmgr.load_app_tasks(self.current_app)
            while not self.get_stop_flag():
            #3. assign tasks
                if self.has_more_work():
                    # schedule tasks to initialized workers
                    availiable_list = self.master.worker_registry.get_availiable_worker_list()
                    if availiable_list:
                        # if list is not empty, then assign task
                        for w in availiable_list:
                            tmptask = self.task_todo_Queue.get_nowait()
                            try:
                                if not self.scheduled_task_queue.has_key(w.wid):
                                    self.scheduled_task_queue[w.wid] = []
                                self.scheduled_task_queue[w.wid].append(tmptask.tid)
                                log.info('TaskScheduler: schedule task=%d to worker=%d', tmptask.tid, w.wid)
                                if not self.master.schedule(w.w_uuid, [tmptask]):
                                    log.error("TaskScheduler: schedule task=%d fail, try again", tmptask.tid)
                                    self.task_todo_Queue.put(tmptask)
                            except Queue.Empty:
                                break
                # monitor task complete status
                    # while True:
                if not self.completed_Queue.empty():
                    t = self.completed_Queue.get()
                    self.appmgr.task_done(self.current_app, t.tid)
                    task_num += 1
                    log.info('TaskScheduler: task=%d complete...', t.tid)
                if len(self.current_app.task_list) == task_num and len(self.scheduled_task_queue) == 0:
                    break

                time.sleep(0.1)
            #self.appmgr.finilize(self.current_app.app_id)
            # logging app finial
            log.info('TaskScheduler: Application complete, ready for next applicaton')
            self.current_app = self.appmgr.next_app()
            # if current_app != None , rerun scheduler.
        log.info('TaskScheduler: No more application, stop scheduler')
        self.processing = False