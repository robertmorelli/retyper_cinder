# richards/advanced  granularity=benchmark
# mask=250665  (10/21 units erased)

"""
based on a Java version:
 Based on original version written in BCPL by Dr Martin Richards
 in 1981 at Cambridge University Computer Laboratory, England
 and a C++ version derived from a Smalltalk version written by
 L Peter Deutsch.
 Java version:  Copyright (C) 1995 Sun Microsystems, Inc.
 Translation from C++, Mario Wolczko
 Outer loop added by Alex Jacoby
"""
from __future__ import annotations
import __static__
from typing import Any
import sys
from __static__ import cast, int64, box, inline
from typing import Optional, List
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
I_IDLE = 1
I_WORK = 2
I_HANDLERA = 3
I_HANDLERB = 4
I_DEVA = 5
I_DEVB = 6
K_DEV = 1000
K_WORK = 1001
BUFSIZE = 4
BUFSIZE_RANGE = range(BUFSIZE)

class Packet(object):

    def __init__(self, l: Optional[Packet], i: int64, k: int64) -> None:
        self.link: Optional[Packet] = l
        self.ident: int64 = i
        self.kind: int64 = k
        self.datum: int = 0
        self.data: List[int] = [0] * BUFSIZE

    def append_to(self, lst: Optional[Packet]):
        self.link = None
        if lst is None:
            return self
        else:
            p: Packet = lst
            next: Optional[Packet] = p.link
            while next is not None:
                p = next
                next = p.link
            p.link = self
            return lst

class TaskRec:
    pass

class DeviceTaskRec(TaskRec):

    def __init__(self) -> None:
        self.pending: Any = None

class IdleTaskRec(TaskRec):

    def __init__(self) -> None:
        self.control: int64 = 1
        self.count: int64 = 10000

class HandlerTaskRec(TaskRec):

    def __init__(self) -> None:
        self.work_in: Any = None
        self.device_in: Any = None

    def workInAdd(self, p: Packet):
        self.work_in = p.append_to(self.work_in)
        return self.work_in

    def deviceInAdd(self, p: Packet):
        self.device_in = p.append_to(self.device_in)
        return self.device_in

class WorkerTaskRec(TaskRec):

    def __init__(self) -> None:
        self.destination = I_HANDLERA
        self.count = 0

class TaskState(object):

    def __init__(self):
        self.packet_pending = True
        self.task_waiting = False
        self.task_holding = False

    def packetPending(self):
        self.packet_pending = True
        self.task_waiting = False
        self.task_holding = False
        return self

    def waiting(self):
        self.packet_pending = False
        self.task_waiting = True
        self.task_holding = False
        return self

    def running(self):
        self.packet_pending = False
        self.task_waiting = False
        self.task_holding = False
        return self

    def waitingWithPacket(self):
        self.packet_pending = True
        self.task_waiting = True
        self.task_holding = False
        return self

    @inline
    def isPacketPending(self):
        return self.packet_pending

    @inline
    def isTaskWaiting(self):
        return self.task_waiting

    @inline
    def isTaskHolding(self):
        return self.task_holding

    @inline
    def isTaskHoldingOrWaiting(self):
        return self.task_holding or (not self.packet_pending and self.task_waiting)

    @inline
    def isWaitingWithPacket(self):
        return self.packet_pending and self.task_waiting and (not self.task_holding)
tracing = False
layout = 0

def trace(a):
    global layout
    layout -= 1
    if layout <= 0:
        print()
        layout = 50
    print(a, end='')
TASKTABSIZE = 10

class TaskWorkArea(object):

    def __init__(self) -> None:
        self.taskTab: List[Task] = [None] * TASKTABSIZE
        self.taskList: Optional[Task] = None
        self.holdCount: int64 = 0
        self.qpktCount: int64 = 0
taskWorkArea = TaskWorkArea()

class Task(TaskState):

    def __init__(self, i, p, w, initialState, r):
        wa = taskWorkArea
        self.link = wa.taskList
        self.ident = i
        self.priority = p
        self.input = w
        self.packet_pending = initialState.isPacketPending()
        self.task_waiting = initialState.isTaskWaiting()
        self.task_holding = initialState.isTaskHolding()
        self.handle = r
        wa.taskList = self
        wa.taskTab[i] = self

    def fn(self, pkt: Optional[Packet], r: TaskRec):
        raise NotImplementedError

    def addPacket(self, p, old):
        if self.input is None:
            self.input = p
            self.packet_pending = True
            if self.priority > old.priority:
                return self
        else:
            p.append_to(self.input)
        return old

    def runTask(self):
        if TaskState.isWaitingWithPacket(cast(TaskState, self)):
            msg = self.input
            if msg is not None:
                self.input = msg.link
                if self.input is None:
                    self.running()
                else:
                    self.packetPending()
        else:
            msg = None
        return self.fn(msg, self.handle)

    def waitTask(self):
        self.task_waiting = True
        return self

    def hold(self):
        taskWorkArea.holdCount += 1
        self.task_holding = True
        return self.link

    def release(self, i):
        t = Task.findtcb(self, int64(i))
        t.task_holding = False
        if t.priority > self.priority:
            return t
        else:
            return self

    def qpkt(self, pkt):
        t = Task.findtcb(self, int64(pkt.ident))
        taskWorkArea.qpktCount += 1
        pkt.link = None
        pkt.ident = self.ident
        return t.addPacket(pkt, self)

    def findtcb(self, id: int64):
        t = taskWorkArea.taskTab[box(id)]
        return t

class DeviceTask(Task):

    def __init__(self, i, p, w, s, r):
        Task.__init__(self, i, p, w, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec):
        d: DeviceTaskRec = cast(DeviceTaskRec, r)
        if pkt is None:
            pkt = d.pending
            if pkt is None:
                return self.waitTask()
            else:
                d.pending = None
                return self.qpkt(pkt)
        else:
            d.pending = pkt
            if tracing:
                trace(pkt.datum)
            return self.hold()

class HandlerTask(Task):

    def __init__(self, i, p, w, s, r):
        Task.__init__(self, i, p, w, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec):
        h: HandlerTaskRec = cast(HandlerTaskRec, r)
        if pkt is not None:
            if pkt.kind == int64(K_WORK):
                h.workInAdd(pkt)
            else:
                h.deviceInAdd(pkt)
        work: Optional[Packet] = h.work_in
        if work is None:
            return self.waitTask()
        count: int = work.datum
        if count >= BUFSIZE:
            h.work_in = work.link
            return self.qpkt(work)
        dev: Optional[Packet] = h.device_in
        if dev is None:
            return self.waitTask()
        h.device_in = dev.link
        dev.datum = work.data[count]
        work.datum = count + 1
        return self.qpkt(dev)

class IdleTask(Task):

    def __init__(self, i, p, w, s, r):
        Task.__init__(self, i, 0, None, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec):
        i: IdleTaskRec = cast(IdleTaskRec, r)
        i.count -= 1
        if i.count == 0:
            return self.hold()
        elif i.control & 1 == 0:
            i.control //= 2
            return Task.release(self, I_DEVA)
        else:
            i.control = i.control // 2 ^ 53256
            return Task.release(self, I_DEVB)
A = 65

class WorkTask(Task):

    def __init__(self, i, p, w, s, r):
        Task.__init__(self, i, p, w, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec):
        w: WorkerTaskRec = cast(WorkerTaskRec, r)
        if pkt is None:
            return self.waitTask()
        if w.destination == I_HANDLERA:
            dest: int64 = int64(I_HANDLERB)
        else:
            dest = int64(I_HANDLERA)
        w.destination = box(dest)
        pkt.ident = dest
        pkt.datum = 0
        i = 0
        while i < BUFSIZE:
            x: int64 = int64(w.count + 1)
            w.count = box(x)
            if w.count > 26:
                w.count = 1
            pkt.data[i] = A + w.count - 1
            i = i + 1
        return self.qpkt(pkt)

def schedule():
    t: Optional[Task] = taskWorkArea.taskList
    while t is not None:
        if tracing:
            print('tcb =', t.ident)
        if TaskState.isTaskHoldingOrWaiting(cast(TaskState, t)):
            t = t.link
        else:
            if tracing:
                trace(chr(ord('0') + t.ident))
            t = t.runTask()

class Richards(object):

    def run(self, iterations: int):
        for i in range(iterations):
            taskWorkArea.holdCount = 0
            taskWorkArea.qpktCount = 0
            IdleTask(I_IDLE, 1, 10000, TaskState().running(), IdleTaskRec())
            wkq: Optional[Packet] = Packet(None, 0, int64(K_WORK))
            wkq = Packet(wkq, 0, int64(K_WORK))
            WorkTask(I_WORK, 1000, wkq, TaskState().waitingWithPacket(), WorkerTaskRec())
            wkq = Packet(None, int64(I_DEVA), int64(K_DEV))
            wkq = Packet(wkq, int64(I_DEVA), int64(K_DEV))
            wkq = Packet(wkq, int64(I_DEVA), int64(K_DEV))
            HandlerTask(I_HANDLERA, 2000, wkq, TaskState().waitingWithPacket(), HandlerTaskRec())
            wkq = Packet(None, int64(I_DEVB), int64(K_DEV))
            wkq = Packet(wkq, int64(I_DEVB), int64(K_DEV))
            wkq = Packet(wkq, int64(I_DEVB), int64(K_DEV))
            HandlerTask(I_HANDLERB, 3000, wkq, TaskState().waitingWithPacket(), HandlerTaskRec())
            wkq = None
            DeviceTask(I_DEVA, 4000, wkq, TaskState().waiting(), DeviceTaskRec())
            DeviceTask(I_DEVB, 5000, wkq, TaskState().waiting(), DeviceTaskRec())
            schedule()
            if taskWorkArea.holdCount == 9297 and taskWorkArea.qpktCount == 23246:
                pass
            else:
                print('err')
                return False
        return True
if __name__ == '__main__':
    num_iterations = 8
    if len(sys.argv) > 1:
        num_iterations = int(sys.argv[1])
    start_time = time.time()
    Richards().run(num_iterations)
    end_time = time.time()
    runtime = end_time - start_time
    print(runtime)
