import wx
import wx.lib.agw.aui as aui
import wx.lib.mixins.listctrl as listmix
import argparse
import time
import types
import queue
from threading import *
from multiprocessing import Process, Queue, current_process, cpu_count
from subprocess import Popen, PIPE, STDOUT
from pathlib import Path
import signal, psutil

theArgs = argparse.Namespace

# Define notification event for thread completion
EVT_WORKER_ID = wx.NewEventType()

D_PROCESSING = -2
D_QUEUED = -1


def EVT_WORKER(win, func):
    """Define Result Event."""
    win.Connect(-1, -1, EVT_WORKER_ID, func)


class WorkerEvent(wx.PyEvent):
    """ event to carry result data."""

    def __init__(self, data):
        """Init Result Event."""
        wx.PyEvent.__init__(self)
        self.SetEventType(EVT_WORKER_ID)
        self.data = data


class Task:
    def __init__(self, args):
        pass

    def calculate(self, args):
        batch, fn, = args
        start = time.time()
        p = Popen([batch, fn], stdout=PIPE, stderr=STDOUT)
        stdout = p.communicate()[0].decode("utf-8").strip()
        retcode = p.poll()
        return [stdout, retcode, time.time() - start]


class Dispatcher:
    """
    The Dispatcher class manages the task and result queues.
    """

    def __init__(self):
        """
        Initialise the Dispatcher.
        """
        self.taskQueue = Queue()
        self.resultQueue = Queue()
        self.processQueue = Queue()

    def putTask(self, task):
        """
        Put a task on the task queue.
        """
        self.taskQueue.put(task)

    def getTask(self):
        """
        Get a task from the task queue.
        """
        return self.taskQueue.get()

    def putResult(self, output):
        """
        Put a result on the result queue.
        """
        self.resultQueue.put(output)

    def getResult(self):
        """
        Get a result from the result queue.
        """
        return self.resultQueue.get()

    def putProcess(self, process):
        """
        Put a process on the process queue.
        """
        self.processQueue.put(process)

    def getProcess(self):
        """
        Get a process from the process queue.
        """
        return self.processQueue.get()


class TaskServerMP:
    """
    The TaskServerMP class provides a target worker class method for queued processes.
    """

    def __init__(
        self,
        processCls,
        numprocesses=1,
        items=[],
        tasks=[],
        results=[],
        notify_window=None,
        dispatcher=None,
    ):
        """
        Initialise the TaskServerMP and create the dispatcher and processes.
        """
        self.numprocesses = numprocesses
        self.Items = items
        self.Tasks = tasks
        self.Results = results
        self.numtasks = len(tasks)
        self._notify_window = notify_window

        # Create the dispatcher
        self.dispatcher = dispatcher

        self.Processes = []

        # The worker processes must be started here!
        for n in range(numprocesses):
            process = Process(
                target=TaskServerMP.worker, args=(self.dispatcher, processCls,)
            )
            process.start()
            self.Processes.append(process)

        self.timeStart = 0.0
        self.timeElapsed = 0.0
        self.timeRemain = 0.0
        self.processTime = {}

        # Set some program flags
        self.keepgoing = True
        self.i = 0
        self.j = 0

    def processTasks(self, post_UIfunc=None):
        """
        Start the execution of tasks by the processes.
        """
        self.keepgoing = True

        self.timeStart = time.time()
        # Set the initial process time for each
        for n in range(self.numprocesses):
            pid_str = "%d" % self.Processes[n].pid
            self.processTime[pid_str] = 0.0

        # Submit first set of tasks
        if self.numprocesses == 0:
            numprocstart = 1
        else:
            numprocstart = min(self.numprocesses, self.numtasks)
        for self.i in range(numprocstart):
            wx.PostEvent(
                self._notify_window,
                WorkerEvent({"type_event": "submit_job", "pos": self.Items[self.i]}),
            )
            self.dispatcher.putTask((self.i, self.Items[self.i],) + self.Tasks[self.i])

        self.j = -1
        self.i = numprocstart - 1
        while self.j < self.i:
            # Get and print results
            output = self.getOutput()
            # Execute some function (Yield to a wx.Button event)
            if isinstance(post_UIfunc, (types.FunctionType, types.MethodType)):
                post_UIfunc(output)
            if (self.keepgoing) and (self.i + 1 < self.numtasks):
                # Submit another task
                self.i += 1
                wx.PostEvent(
                    self._notify_window,
                    WorkerEvent(
                        {"type_event": "submit_job", "pos": self.Items[self.i]}
                    ),
                )
                self.dispatcher.putTask(
                    (self.i, self.Items[self.i],) + self.Tasks[self.i]
                )

    def processStop(self, post_UIfunc=None):
        """
        Stop the execution of tasks by the processes.
        """
        self.keepgoing = False

        while self.j < self.i:
            # Get and print any results remaining in the done queue
            output = self.getOutput()
            if isinstance(post_UIfunc, (types.FunctionType, types.MethodType)):
                post_UIfunc(output)

    def processTerm(self):
        """
        Stop the execution of tasks by the processes.
        """
        for n in range(self.numprocesses):
            # Terminate any running processes
            self.Processes[n].terminate()

        # Wait for all processes to stop
        while self.anyAlive():
            time.sleep(0.5)

    def anyAlive(self):
        """
        Check if any processes are alive.
        """
        isalive = False
        for n in range(self.numprocesses):
            isalive = isalive or self.Processes[n].is_alive()
        return isalive

    def getOutput(self):
        """
        Get the output from one completed task.
        """
        self.j += 1

        if self.numprocesses == 0:
            # Use the single-process method
            self.worker_sp()

        output = self.dispatcher.getResult()
        self.Results[output["num"]] = output["result"]

        # Calculate the time remaining
        self.timeRemaining(self.j + 1, self.numtasks, output["process"]["pid"])

        return output

    def timeRemaining(self, tasknum, numtasks, pid):
        """
        Calculate the time remaining for the processes to complete N tasks.
        """
        timeNow = time.time()
        self.timeElapsed = timeNow - self.timeStart

        pid_str = "%d" % pid
        self.processTime[pid_str] = self.timeElapsed

        # Calculate the average time elapsed for all of the processes
        timeElapsedAvg = 0.0
        numprocesses = self.numprocesses
        if numprocesses == 0:
            numprocesses = 1
        for pid_str in self.processTime.keys():
            timeElapsedAvg += self.processTime[pid_str] / numprocesses
        self.timeRemain = timeElapsedAvg * (float(numtasks) / float(tasknum) - 1.0)

    def worker(cls, dispatcher, processCls):
        """
        The worker creates a processCls object to calculate the result.
        """
        while True:
            args = dispatcher.getTask()
            dispatcher.putProcess(
                {"num": args[0], "pos": args[1], "pid": current_process().pid}
            )
            taskproc = processCls(args[2])
            result = taskproc.calculate(args[3])
            output = {
                "process": {
                    "name": current_process().name,
                    "pid": current_process().pid,
                },
                "num": args[0],
                "pos": args[1],
                "args": args[3],
                "result": result,
            }
            # Put the result on the output queue
            dispatcher.putResult(output)

    # The multiprocessing worker must not require any existing object for execution!
    worker = classmethod(worker)

    def worker_sp(self, processCls):
        """
        A single-process version of the worker method.
        """
        args = self.dispatcher.getTask()
        dispatcher.putProcess({"num": args[0], "pos": args[1], "pid": 0})
        taskproc = processCls(args[2])
        result = taskproc.calculate(args[3])
        output = {
            "process": {"name": "Process-0", "pid": 0},
            "num": args[0],
            "pos": args[1],
            "args": args[3],
            "result": result,
        }
        # Put the result on the output queue
        self.dispatcher.putResult(output)

    def post_updateUI(self, output):
        """
        Get and print the results from one completed task.
        """
        wx.PostEvent(
            self._notify_window,
            WorkerEvent(
                {
                    "type_event": "output_job",
                    "step": self.j + 1,
                    "total": self.numtasks,
                    "remaining_time": self.timeRemain,
                    "output": output,
                }
            ),
        )

        if self._notify_window.want_abort():
            # Stop processing tasks
            self.processStop(self.post_updateUI)

    def run(self):
        """
        Run the TaskServerMP - start, stop & terminate processes.
        """
        self.processTasks(self.post_updateUI)
        if self.numprocesses > 0:
            self.processTerm()


class myListCtrl(wx.ListCtrl, listmix.ColumnSorterMixin):
    def __init__(self, parent, pos=wx.DefaultPosition, size=wx.DefaultSize, style=0):
        wx.ListCtrl.__init__(self, parent, pos=pos, size=size, style=style)
        listmix.ColumnSorterMixin.__init__(self, 4)

        self.InsertColumn(0, "Job#", width=40)
        self.InsertColumn(1, "Filename", width=150)
        self.InsertColumn(2, "Return code", width=120)
        self.InsertColumn(3, "Duration", width=70)

        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self.SelectCb)
        self.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.SelectCb)
        self.Bind(wx.EVT_LIST_BEGIN_LABEL_EDIT, self.OnBeginLabelEdit)
        self.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.ItemRightClickCb)
        self.Bind(wx.EVT_CHAR, self.onKeyPress)

        self.listdata = {}
        self.itemDataMap = self.listdata

    def GetListCtrl(self):
        return self

    def SelectCb(self, event):
        self.GetParent().GetParent().GetParent().SetStdout(event.GetIndex())

    def onKeyPress(self, event):
        keycode = event.GetKeyCode()
        if keycode == wx.WXK_CONTROL_A:
            self.Freeze()
            item = -1
            while 1:
                item = self.GetNextItem(item)
                if item == -1:
                    break
                self.SetItemState(item, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
            self.Thaw()
            event.Skip()
        elif keycode:
            event.Skip()

    def OnBeginLabelEdit(self, event):
        event.Veto()

    def ItemRightClickCb(self, event):
        if not self.GetSelectedItemCount():
            return
        selected = get_selected_items(self)

        foundProcessing = False
        foundDone = False
        for row_id in selected:
            pos = self.GetItemData(row_id)  # 0-based unsorted index
            if self.listdata[pos][2] == D_PROCESSING:
                foundProcessing = True
            elif self.listdata[pos][2] != D_QUEUED:
                foundDone = True
        if not foundProcessing and not foundDone:
            return
        menu = wx.Menu()
        if foundProcessing:
            mnu_kill = wx.MenuItem(
                menu, wx.ID_ANY, "&Kill job(s)\tShift+Del", "Kill job(s)"
            )
            menu.Append(mnu_kill)
            self.Bind(wx.EVT_MENU, self.KillSelectionCb, mnu_kill)
            mnu_restart = wx.MenuItem(
                menu, wx.ID_ANY, "&Restart job(s)", "Restart job(s)"
            )
            menu.Append(mnu_restart)
            self.Bind(wx.EVT_MENU, self.RestartSelectionCb, mnu_restart)
        elif foundDone:
            mnu_restart = wx.MenuItem(
                menu, wx.ID_ANY, "&Restart job(s)", "Restart job(s)"
            )
            menu.Append(mnu_restart)
            self.Bind(wx.EVT_MENU, self.RestartSelectionCb, mnu_restart)
        self.PopupMenu(menu, event.GetPoint())
        menu.Destroy()

    def KillSelectionCb(self, event):
        selected = get_selected_items(self)

        for row_id in selected:
            pos = self.GetItemData(row_id)  # 0-based unsorted index
            if self.listdata[pos][2] != D_PROCESSING:
                continue
            pid = self.listdata[pos][5]
            if pid:
                kill_child_processes(pid)

    def RestartSelectionCb(self, event):
        selected = get_selected_items(self)

        files = []
        items = []
        for row_id in selected:
            pos = self.GetItemData(row_id)  # 0-based unsorted index
            if self.listdata[pos][2] == D_PROCESSING:
                pid = self.listdata[pos][5]
                if pid:
                    kill_child_processes(pid)
                items.append(pos)
                files.append(self.listdata[pos][1])
                self.SetItem(row_id, 2, "Queued")
            elif self.listdata[pos][2] != D_QUEUED:
                items.append(pos)
                files.append(self.listdata[pos][1])
                self.SetItem(row_id, 2, "Queued")

        main_wnd = self.GetParent().GetParent().GetParent()

        main_wnd.timeStart = time.time()
        main_wnd.timer.Start(1000)

        main_wnd.dispatcher = Dispatcher()
        main_wnd.worker = WorkerThread(
            main_wnd, main_wnd.dispatcher, theArgs.script, items, files
        )
        main_wnd.workerpid = WorkerThreadPID(main_wnd, main_wnd.dispatcher)


def get_selected_items(list_control):
    """
    Gets the selected items for the list control.
    Selection is returned as a list of selected indices,
    low to high.
    """

    selection = []

    # start at -1 to get the first selected item
    item = -1
    while True:
        item = GetNextSelected(list_control, item)
        if item == -1:
            return selection

        selection.append(item)


def GetNextSelected(list_control, item):
    """Returns next selected item, or -1 when no more"""

    return list_control.GetNextItem(item, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED)


def pretty_time_delta(seconds):
    milliseconds = int((seconds - int(seconds)) * 1000)
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days > 0:
        return "%dd%dh%dm%ds" % (days, hours, minutes, seconds)
    elif hours > 0:
        return "%dh%dm%ds" % (hours, minutes, seconds)
    elif minutes > 0:
        return "%dm%ds" % (minutes, seconds)
    elif seconds > 0:
        return "%d.%ds" % (seconds, milliseconds,)
    else:
        return "%dms" % (milliseconds,)


def kill_child_processes(parent_pid, sig=signal.SIGTERM):
    try:
        parent = psutil.Process(parent_pid)
    except psutil.NoSuchProcess:
        return
    children = parent.children(recursive=True)
    for process in children:
        process.send_signal(sig)


class MainPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, name="mainpanel", style=wx.WANTS_CHARS)

        self.parent = parent
        self.mgr = aui.AuiManager()
        self.mgr.SetManagedWindow(self)

        panel_top = wx.Panel(parent=self)

        numtasks = len(theArgs.files)
        self.panel_progress = wx.Panel(parent=panel_top)
        self.progress_step = wx.StaticText(
            self.panel_progress,
            label="Completed: %2d / %2d (%d%%)"
            % (0, numtasks, 100.0 * (float(0) / float(numtasks))),
        )
        self.progress_elapsed = wx.StaticText(
            self.panel_progress, label="Time Elapsed: %s" % (pretty_time_delta(0.0))
        )
        self.progress_remaining = wx.StaticText(
            self.panel_progress, label="Remaining: %s" % (pretty_time_delta(0.0))
        )
        self.progress = wx.Gauge(self.panel_progress)
        self.cancelbutton = wx.Button(self.panel_progress, label="Abort")

        panel_progress_sizer2 = wx.BoxSizer(wx.VERTICAL)

        panel_progress_text_sizer = wx.BoxSizer(wx.HORIZONTAL)
        panel_progress_text_sizer.Add(
            self.progress_step, 1, wx.CENTER | wx.LEFT | wx.RIGHT, 20
        )
        panel_progress_text_sizer.Add(
            self.progress_elapsed, 1, wx.CENTER | wx.LEFT | wx.RIGHT, 20
        )
        panel_progress_text_sizer.Add(
            self.progress_remaining, 1, wx.CENTER | wx.LEFT | wx.RIGHT, 20
        )

        panel_progress_sizer = wx.BoxSizer(wx.HORIZONTAL)
        panel_progress_sizer.Add(self.progress, 1, wx.EXPAND | wx.ALL)
        panel_progress_sizer.Add(self.cancelbutton, 0, wx.EXPAND | wx.ALL)

        panel_progress_sizer2.Add(panel_progress_text_sizer, 1, wx.EXPAND | wx.ALL)
        panel_progress_sizer2.Add(panel_progress_sizer, 1, wx.EXPAND | wx.ALL)

        self.panel_progress.SetSizer(panel_progress_sizer2)

        self.cancelbutton.Bind(wx.EVT_BUTTON, self.cancel)
        EVT_WORKER(self, self.OnWorkerEvent)

        panel_list = wx.Panel(parent=panel_top)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.panel_progress, 0, wx.EXPAND)
        top_sizer.Add(panel_list, 1, wx.EXPAND)
        panel_top.SetSizer(top_sizer)

        self.list_ctrl = myListCtrl(
            panel_list,
            size=(-1, -1),
            style=wx.LC_REPORT | wx.BORDER_SUNKEN | wx.LC_EDIT_LABELS,
        )
        self.list_ctrl.Freeze()
        for i in range(numtasks):
            self.list_ctrl.listdata[i] = [i, theArgs.files[i], D_QUEUED, 0, "", 0]
            self.list_ctrl.InsertItem(i, str(i + 1), -1)
            self.list_ctrl.SetItem(i, 1, Path(theArgs.files[i]).name)
            self.list_ctrl.SetItem(i, 2, "Queued")
            self.list_ctrl.SetItemData(i, i)
        self.list_ctrl.Thaw()

        panel_list_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_list_sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 0)
        panel_list.SetSizer(panel_list_sizer)

        self.log = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)

        self.mgr.AddPane(panel_top, aui.AuiPaneInfo().Name("pane_list").CenterPane())
        self.mgr.AddPane(
            self.log,
            aui.AuiPaneInfo()
            .CloseButton(True)
            .Name("pane_output")
            .Caption("Output")
            .BestSize(-1, 200)
            .Bottom(),
        )
        self.mgr.Update()

        wx.Log.SetActiveTarget(wx.LogTextCtrl(self.log))
        wx.Log.DisableTimestamp()

        self.timeStart = time.time()
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.onTimer, self.timer)
        self.timer.Start(1000)

        self.dispatcher = Dispatcher()
        self.worker = WorkerThread(
            self,
            self.dispatcher,
            theArgs.script,
            range(len(theArgs.files)),
            theArgs.files,
        )
        self.workerpid = WorkerThreadPID(self, self.dispatcher)

    def OnWorkerEvent(self, event):
        """Show worker status."""
        if event.data is None:
            self.timer.Stop()
            self.panel_progress.Hide()
            self.Layout()
        elif event.data["type_event"] == "submit_job":
            pos = event.data["pos"]
            row_id = self.list_ctrl.FindItem(-1, pos)
            self.list_ctrl.listdata[pos][2] = D_PROCESSING
            self.list_ctrl.listdata[pos][3] = time.time()
            self.list_ctrl.listdata[pos][5] = 0
            self.list_ctrl.SetItem(row_id, 2, "Processing...")
            self.list_ctrl.SetItemBackgroundColour(
                row_id, wx.Colour("MEDIUM GOLDENROD")
            )
        elif event.data["type_event"] == "submit_pid":
            pos = event.data["pos"]
            row_id = self.list_ctrl.FindItem(-1, pos)
            self.list_ctrl.SetItem(row_id, 2, "Processing...(%d)" % event.data["pid"])
            self.list_ctrl.listdata[pos][5] = event.data["pid"]
        elif event.data["type_event"] == "output_job":
            output = event.data["output"]
            pos = output["pos"]
            row_id = self.list_ctrl.FindItem(-1, pos)
            stdout = str(output["result"][0])
            retcode = output["result"][1]
            duration = output["result"][2]
            self.list_ctrl.listdata[pos][2] = retcode
            self.list_ctrl.listdata[pos][3] = duration
            self.list_ctrl.listdata[pos][4] = stdout
            self.list_ctrl.listdata[pos][5] = 0
            self.list_ctrl.SetItem(row_id, 2, "Done (%d)" % retcode)
            self.list_ctrl.SetItem(row_id, 3, pretty_time_delta(duration))
            if retcode:
                self.list_ctrl.SetItemBackgroundColour(
                    row_id, wx.Colour("MEDIUM VIOLET RED")
                )
            else:
                self.list_ctrl.SetItemBackgroundColour(
                    row_id, wx.Colour("GREEN YELLOW")
                )

            # Update progression
            step = event.data["step"]
            total = event.data["total"]
            remaining_time = event.data["remaining_time"]
            self.progress.SetRange(total)
            self.progress.SetValue(step)
            self.progress_step.SetLabel(
                "Completed: %2d / %2d (%d%%)"
                % (step, total, 100.0 * (float(step) / float(total)))
            )
            self.progress_remaining.SetLabel(
                "Remaining: %s" % (pretty_time_delta(remaining_time))
            )

    def SetStdout(self, row_id):
        self.log.Clear()
        if self.list_ctrl.GetSelectedItemCount():
            self.log.Freeze()
            selected = get_selected_items(self.list_ctrl)
            for row_id in selected:
                pos = self.list_ctrl.GetItemData(row_id)  # 0-based unsorted index
                self.log.AppendText(self.list_ctrl.listdata[pos][4])
                self.log.AppendText("\n")
            self.log.Thaw()

    def cancel(self, evt):
        self.cancelbutton.SetLabel("Aborting...")
        self.worker.abort()

    def want_abort(self):
        return self.worker._want_abort

    def onTimer(self, event):
        timeNow = time.time()
        timeElapsed = timeNow - self.timeStart
        self.progress_elapsed.SetLabel(
            "Time Elapsed: %s" % (pretty_time_delta(timeElapsed))
        )

        # Update time for processing item
        row_id = -1
        while True:  # loop all the processing items
            row_id = self.list_ctrl.GetNextItem(row_id)
            if row_id == -1:
                break
            pos = self.list_ctrl.GetItemData(row_id)  # 0-based unsorted index
            if self.list_ctrl.listdata[pos][2] == D_PROCESSING:
                timeElapsed = timeNow - self.list_ctrl.listdata[pos][3]
                self.list_ctrl.SetItem(row_id, 3, pretty_time_delta(timeElapsed))


class WorkerThread(Thread):
    """Worker Thread Class."""

    def __init__(self, notify_window, dispatcher, script, items, files):
        """Init Worker Thread Class."""
        Thread.__init__(self)
        self._notify_window = notify_window
        self._want_abort = False
        self.dispatcher = dispatcher
        self.script = script
        self.items = items
        self.files = files
        self.start()

    def run(self):
        """Run Worker Thread."""
        numtasks = len(self.files)

        # Create the task list
        Tasks = [((), ()) for i in range(numtasks)]
        Results = [None for i in range(numtasks)]
        for i in range(numtasks):
            Tasks[i] = (
                (),
                (self.script, self.files[i],),
            )

        self.ts = TaskServerMP(
            processCls=Task,
            numprocesses=theArgs.cpu,
            items=self.items,
            tasks=Tasks,
            results=Results,
            notify_window=self._notify_window,
            dispatcher=self.dispatcher,
        )
        self.ts.run()
        wx.PostEvent(self._notify_window, WorkerEvent(None))

    def abort(self):
        """abort worker thread."""
        # Method for use by main thread to signal an abort
        self._want_abort = True


class WorkerThreadPID(Thread):
    """Worker Thread Class."""

    def __init__(self, notify_window, dispatcher):
        """Init Worker Thread Class."""
        Thread.__init__(self)
        self._notify_window = notify_window
        self._want_abort = False
        self.dispatcher = dispatcher
        self.daemon = True
        self.start()

    def run(self):
        """Run Worker Thread."""
        while True:
            process = self.dispatcher.getProcess()
            wx.PostEvent(
                self._notify_window,
                WorkerEvent(
                    {
                        "type_event": "submit_pid",
                        "pos": process["pos"],
                        "pid": process["pid"],
                    }
                ),
            )


class MainFrame(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(
            self,
            None,
            title=Path(theArgs.script).stem,
            pos=wx.Point(-10, 0),
            size=wx.Size(500, 800),
        )

        self.panel = MainPanel(self)
        self.Bind(wx.EVT_CLOSE, self.OnQuit)
        self.Show(True)

    def OnQuit(self, event):
        self.panel.worker.ts.processTerm()
        self.panel.mgr.UnInit()
        self.Destroy()


def main():
    global theArgs
    """Launch main application """
    parser = argparse.ArgumentParser(description="Loop a CMD script on files.")
    parser.add_argument(
        "--cpu",
        type=int,
        default=cpu_count(),
        help="number of process (default = number of CPU)",
    )
    parser.add_argument(
        "--script", required=True, help="batch to launch on every other files"
    )
    parser.add_argument("files", nargs="+", help="files to process")
    theArgs = parser.parse_args()

    app = wx.App(False)
    frm = MainFrame()
    app.SetTopWindow(frm)

    app.MainLoop()


if __name__ == "__main__":
    main()
