# richards/advanced  granularity=benchmark
# mask=4055039  (20/22 units erased)

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
from __static__ import cast, cbool, int64, box, inline
from typing import Optional
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
I_IDLE: Any = 1
I_WORK: Any = 2
I_HANDLERA: Any = 3
I_HANDLERB: Any = 4
I_DEVA: Any = 5
I_DEVB: Any = 6
K_DEV: Any = 1000
K_WORK: Any = 1001
BUFSIZE: Any = 4
BUFSIZE_RANGE: Any = range(BUFSIZE)

class Packet(object):

    def __init__(self, l: Any, i: Any, k: Any) -> None:
        self.link: Any = l
        self.ident: Any = i
        self.kind: Any = k
        self.datum: Any = 0
        self.data: Any = [0] * BUFSIZE

    def append_to(self, lst: Any) -> Any:
        self.link = None
        if lst is None:
            return self
        else:
            p: Any = lst
            next: Any = p.link
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
        self.control: Any = 1
        self.count: Any = 10000

class HandlerTaskRec(TaskRec):

    def __init__(self) -> None:
        self.work_in: Any = None
        self.device_in: Any = None

    def workInAdd(self, p: Any) -> Any:
        self.work_in = p.append_to(self.work_in)
        return self.work_in

    def deviceInAdd(self, p: Any) -> Any:
        self.device_in = p.append_to(self.device_in)
        return self.device_in

class WorkerTaskRec(TaskRec):

    def __init__(self) -> None:
        self.destination: Any = I_HANDLERA
        self.count: Any = 0

class TaskState(object):

    def __init__(self) -> Any:
        self.packet_pending: Any = True
        self.task_waiting: Any = False
        self.task_holding: Any = False

    def packetPending(self) -> Any:
        self.packet_pending = True
        self.task_waiting = False
        self.task_holding = False
        return self

    def waiting(self) -> Any:
        self.packet_pending = False
        self.task_waiting = True
        self.task_holding = False
        return self

    def running(self) -> Any:
        self.packet_pending = False
        self.task_waiting = False
        self.task_holding = False
        return self

    def waitingWithPacket(self) -> Any:
        self.packet_pending = True
        self.task_waiting = True
        self.task_holding = False
        return self

    @inline
    def isPacketPending(self) -> Any:
        return self.packet_pending

    @inline
    def isTaskWaiting(self) -> Any:
        return self.task_waiting

    @inline
    def isTaskHolding(self) -> Any:
        return self.task_holding

    @inline
    def isTaskHoldingOrWaiting(self) -> Any:
        return box(cbool(self.task_holding) or (cbool(not self.packet_pending) and cbool(self.task_waiting)))

    @inline
    def isWaitingWithPacket(self) -> Any:
        return box(cbool(self.packet_pending) and cbool(self.task_waiting) and cbool(not self.task_holding))
tracing: Any = False
layout = 0

def trace(a: Any) -> Any:
    global layout
    layout -= 1
    if layout <= 0:
        print()
        layout = 50
    print(a, end='')
TASKTABSIZE: Any = 10

class TaskWorkArea(object):

    def __init__(self) -> None:
        self.taskTab: Any = [None] * TASKTABSIZE
        self.taskList: Any = None
        self.holdCount: Any = 0
        self.qpktCount: Any = 0
taskWorkArea: Any = TaskWorkArea()

class Task(TaskState):

    def __init__(self, i: Any, p: Any, w: Any, initialState: Any, r: Any) -> Any:
        wa: Any = taskWorkArea
        self.link: Any = wa.taskList
        self.ident: Any = i
        self.priority: Any = p
        self.input: Any = w
        self.packet_pending = initialState.isPacketPending()
        self.task_waiting = initialState.isTaskWaiting()
        self.task_holding = initialState.isTaskHolding()
        self.handle = r
        wa.taskList = self
        wa.taskTab[i] = self

    def fn(self, pkt: Optional[Packet], r: TaskRec) -> Any:
        raise NotImplementedError

    def addPacket(self, p: Any, old: Any) -> Any:
        if self.input is None:
            self.input = p
            self.packet_pending = True
            if int64(self.priority) > int64(old.priority):
                return self
        else:
            p.append_to(self.input)
        return old

    def runTask(self) -> Any:
        if TaskState.isWaitingWithPacket(cast(TaskState, self)):
            msg: Any = self.input
            if msg is not None:
                self.input = msg.link
                if self.input is None:
                    self.running()
                else:
                    self.packetPending()
        else:
            msg = None
        return self.fn(msg, self.handle)

    def waitTask(self) -> Any:
        self.task_waiting = True
        return self

    def hold(self) -> Any:
        taskWorkArea.holdCount += 1
        self.task_holding = True
        return self.link

    def release(self, i: Any) -> Any:
        t: Any = Task.findtcb(self, i)
        t.task_holding = False
        if int64(t.priority) > int64(self.priority):
            return t
        else:
            return self

    def qpkt(self, pkt: Packet) -> Any:
        t: Task = Task.findtcb(self, pkt.ident)
        taskWorkArea.qpktCount += 1
        pkt.link = None
        pkt.ident = self.ident
        return t.addPacket(pkt, self)

    def findtcb(self, id: Any) -> Any:
        t = taskWorkArea.taskTab[id]
        return t

class DeviceTask(Task):

    def __init__(self, i: Any, p: Any, w: Any, s: Any, r: Any) -> Any:
        Task.__init__(self, i, p, w, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec) -> Any:
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

    def __init__(self, i: Any, p: Any, w: Any, s: Any, r: Any) -> Any:
        Task.__init__(self, i, p, w, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec) -> Any:
        h: HandlerTaskRec = cast(HandlerTaskRec, r)
        if pkt is not None:
            if int64(pkt.kind) == int64(K_WORK):
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

    def __init__(self, i: Any, p: Any, w: Any, s: Any, r: Any) -> Any:
        Task.__init__(self, i, 0, None, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec) -> Any:
        i: IdleTaskRec = cast(IdleTaskRec, r)
        i.count -= 1
        if int64(i.count) == 0:
            return self.hold()
        elif int64(i.control) & 1 == 0:
            i.control //= 2
            return Task.release(self, I_DEVA)
        else:
            i.control = i.control // 2 ^ 53256
            return Task.release(self, I_DEVB)
A: Any = 65

class WorkTask(Task):

    def __init__(self, i: Any, p: Any, w: Any, s: Any, r: Any) -> Any:
        Task.__init__(self, i, p, w, s, r)

    def fn(self, pkt: Optional[Packet], r: TaskRec) -> Any:
        w: WorkerTaskRec = cast(WorkerTaskRec, r)
        if pkt is None:
            return self.waitTask()
        if int64(w.destination) == int64(I_HANDLERA):
            dest: int64 = int64(I_HANDLERB)
        else:
            dest = int64(I_HANDLERA)
        w.destination = box(dest)
        pkt.ident = box(dest)
        pkt.datum = 0
        i = 0
        while i < BUFSIZE:
            x: int64 = int64(w.count) + 1
            w.count = box(x)
            if int64(w.count) > 26:
                w.count = 1
            pkt.data[i] = A + w.count - 1
            i = i + 1
        return self.qpkt(pkt)

def schedule() -> Any:
    t: Any = taskWorkArea.taskList
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

    def run(self, iterations: Any) -> Any:
        for i in range(iterations):
            taskWorkArea.holdCount = 0
            taskWorkArea.qpktCount = 0
            IdleTask(I_IDLE, 1, 10000, TaskState().running(), IdleTaskRec())
            wkq: Any = Packet(None, 0, K_WORK)
            wkq = Packet(wkq, 0, K_WORK)
            WorkTask(I_WORK, 1000, wkq, TaskState().waitingWithPacket(), WorkerTaskRec())
            wkq = Packet(None, I_DEVA, K_DEV)
            wkq = Packet(wkq, I_DEVA, K_DEV)
            wkq = Packet(wkq, I_DEVA, K_DEV)
            HandlerTask(I_HANDLERA, 2000, wkq, TaskState().waitingWithPacket(), HandlerTaskRec())
            wkq = Packet(None, I_DEVB, K_DEV)
            wkq = Packet(wkq, I_DEVB, K_DEV)
            wkq = Packet(wkq, I_DEVB, K_DEV)
            HandlerTask(I_HANDLERB, 3000, wkq, TaskState().waitingWithPacket(), HandlerTaskRec())
            wkq = None
            DeviceTask(I_DEVA, 4000, wkq, TaskState().waiting(), DeviceTaskRec())
            DeviceTask(I_DEVB, 5000, wkq, TaskState().waiting(), DeviceTaskRec())
            schedule()
            if int64(taskWorkArea.holdCount) == 9297 and int64(taskWorkArea.qpktCount) == 23246:
                pass
            else:
                print('err')
                return False
        return True

def main() -> Any:
    num_iterations = 8
    if len(sys.argv) > 1:
        num_iterations = int(sys.argv[1])
    start_time = time.time()
    Richards().run(num_iterations)
    end_time = time.time()
    runtime = end_time - start_time
    print(runtime)
if __name__ == '__main__':
    main()
