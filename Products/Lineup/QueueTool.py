"""
QueueTool.py - tool for storing and managing simple event queue

Author: J Cameron Cooper (jccooper@rice.edu) and Brian N West (bnwest@rice.edu)
copyright (C) 2009, Rice University. All rights reserved.

This software is subject to the provisions of the GNU Lesser General
Public License Version 2.1 (LGPL).  See LICENSE.txt for details.
"""

from Products.CMFCore.utils import UniqueObject
from OFS.SimpleItem import SimpleItem
from Globals import InitializeClass
from ZODB.PersistentList import PersistentList
from Products.PageTemplates.PageTemplateFile import PageTemplateFile

from datetime import datetime, timedelta
from socket import getfqdn
import os
import transaction
import AccessControl
from threading import Lock

# from getipaddr import getipaddr

ManagePermission = 'View management screens'

# need mutex lock to control the adding/removing to the pendingRequests/processingRequests queues

# request getting added to the pendingRequests during publish events via calls to QueueTool.add().
# the publishing action owns the transaction.

# requests get moved from pendingRequests to processingRequests by QueueTool.clockTick() which is
# called by the ClockServer at 3 second intervals.  the ZMI UI also allows request to be moved via
# method manage_cut_in_line; the moved requests are then run on the frontend serving the ZMI.

# after being placed in the processingRequests queue, a child process (zctl script) is spawned
# which performs the work.  the child at startup calls QueueTool.start() which updates the request.
# the child at exit calls QueueTool.stop() which removes the request from processingRequests queue.

mutex = Lock()

import zLOG
def log(msg, severity=zLOG.INFO):
    zLOG.LOG("QueueTool: ", severity, msg)

class QueueTool(UniqueObject, SimpleItem):
    """
    Tool for storage of Print files
    """

    id = 'queue_tool'
    meta_type = 'Queue Tool'

    manage_options = (({'label':'Overview', 'action':'manage_overview'},
                       {'label':'Configure', 'action':'manage_configure'}
                      )+ SimpleItem.manage_options
                     )
    manage_overview = PageTemplateFile('zpt/manage_overview_queue_tool.zpt', globals())
    manage_configure = PageTemplateFile('zpt/manage_configure_queue_tool.zpt', globals())

    security = AccessControl.ClassSecurityInfo()

    # set default time window to 1 hour
    DEFAULT_PROCESS_TIME_WINDOW = 3600


    def __init__(self):
        """Initialize (singleton!) tool object."""
        # user configurable
        self.dictServers        = {}
        self.secProcessWindow   = self.DEFAULT_PROCESS_TIME_WINDOW
        # unknown to the user
        self.pendingRequests    = PersistentList([])
        self.processingRequests = PersistentList([])
        log('__init__ completed.') # this no workeee???


    security.declareProtected(ManagePermission, 'manage_queue_tool')
    def manage_queue_tool(self, dictServers={}, secProcessWindow=DEFAULT_PROCESS_TIME_WINDOW):
        """
        Post creation configuration.  See manage_configure_queue_tool.zpt
        """
        self.dictServers  = eval(dictServers) # string not dictionary returned
        try:
            self.secProcessWindow = int(secProcessWindow)
        except ValueError:
            self.secProcessWindow = self.DEFAULT_PROCESS_TIME_WINDOW


    security.declareProtected(ManagePermission, 'add')
    def add(self, key, dictParams, callbackrequesthandler, priority=1):
        """
        Add a request to the Pending Queue.
        """
        # add() acquires mutex lock.  caller is repsonsible for commiting the transaction.
        mutex.acquire()
        try:
            iListIndex = self.find(key, self.pendingRequests)
            if iListIndex is not None:
                # remove duplicate
                del self.pendingRequests[iListIndex]

            dictRequest = dictParams.copy()

            dictRequest['key'] = key
            dictRequest['requestHandler'] = callbackrequesthandler
            dictRequest['timeRequestMade'] = datetime.now()
            dictRequest['priority'] = priority
            # Walk list, insert immediately before first entry that is lower priority (higher value)
            for i,req in enumerate(self.pendingRequests):
                if req['priority'] > priority:
                    self.pendingRequests.insert(i,req)
                    break
            else: 
                # didn't find one, tack on the end
                self.pendingRequests.append(dictRequest)
            # note that we explicitly and purposely do not commit the transaction here.
            # the caller is likely to be in a module/collection publish transaction.
            # when the caller's transaction commits, the QueueTool change (i.e. adding
            # a request) will also commit.  thus, the request can not be processed until
            # after the publish transaction has completed (i.e. no race condition).
        finally:
            mutex.release()

    security.declarePrivate('find')
    def find(self, strKey, listRequests):
        """
        Find key in persistent list, looking for duplicates.  Return index if found.
        """
        for i in range(0,len(listRequests)):
            if listRequests[i]['key'] == strKey:
                return i
        return None


    security.declarePrivate('gotRequest')
    def gotRequest(self):
        """
        Determine if any requests are ready for processing.
        """
        # requests must be pending ...
        if len(self.pendingRequests) == 0:
            return False

        # our host server can not be maxed out ...
        hostName = getfqdn()
        listHostProcess = [entry for entry in self.processingRequests if 'hostName' in entry and entry['hostName'] == hostName]
        if listHostProcess is not None:
            iProcessMax = hostName in self.dictServers and self.dictServers[hostName] or 1
            if len(listHostProcess) >= iProcessMax:
                return False

        return True

    security.declarePrivate('getRequest')
    def getRequest(self, key=None):
        """
        Get a request from the pending queue and put it in the processing queue or
        get an request already in the processing queue via key.
        """
        # caller must acquire mutex lock.  caller is repsonsible for commiting the transaction.
        if key is None:
            # get a request from the pending queue and put it in the processing queue
            if self.gotRequest():
                dictRequest = self.pendingRequests[0]
                del self.pendingRequests[0]
                self.processingRequests.append(dictRequest)
                return dictRequest
            else:
                return None
        else:
            # get an request already in the processing queue
            iListIndex = self.find(key, self.processingRequests)
            if iListIndex is not None:
                dictRequest = self.processingRequests[iListIndex]
                return dictRequest
            else:
                return None


    security.declarePrivate('start')
    def start(self, key):
        """
        Start processing the queued request.
        """
        # start() acquires mutex lock and is repsonsible for commiting the transaction.
        return


    security.declarePrivate('stop')
    def stop(self, key):
        """
        Stop processing the queued request.
        """
        # stop() acquires mutex lock and is repsonsible for commiting the transaction.
        mutex.acquire()
        try:
            self.removeRequest(key)
            # should only commit the above change
            transaction.commit()
            #import pdb; pdb.set_trace()
            #savepoint = transaction.savepoint()
            #for i in range(0,3):
            #    try:
            #        self.removeRequest(key)
            #        # should only commit the above change
            #        transaction.commit()
            #    except Exception, e:
            #        print "failure:", e
            #        self.plone_log("... QueueTool.stop() failed to commit removing the reuest from the processing queue ... %s ..." % str(e))
            #        savepoint.rollback()
            #        pass
        finally:
            mutex.release()


    security.declarePrivate('removeRequest')
    def removeRequest(self, key):
        """
        Remove a request from the processing queue.
        """
        # caller must acquire mutex lock.  caller is repsonsible for commiting the transaction.
        iListIndex = self.find(key, self.processingRequests)
        if iListIndex is not None:
            del self.processingRequests[iListIndex]


    security.declarePrivate('cleanupProcessing')
    def cleanupProcessing(self):
        """
        Cleanup the processingRequests queue.  Assume that processing entries that
        do not complete in a defined time window cored and did not call removeRequest().
        """
        # cleanupProcessing() acquires mutex lock and is responsible for commiting the transaction.
        portal = self.portal_url.getPortalObject()

        mutex.acquire()
        try:
            timeNow = datetime.now()
            bChanged = False
            for request in self.processingRequests:
                timeProcessStarted = 'timeRequestProcessed' in request and request['timeRequestProcessed'] or None
                childPid = request['pid'] or None
                if childPid:
                    try:
                        os.kill(childPid, 0)
                    except OSError:
                        if timeProcessStarted is not None:
                            self._email_cleanup_message(request)
                            bChanged = True

                if timeProcessStarted is not None:
                    timeProcessing = timeNow - timeProcessStarted
                    timeProcessingMax = timedelta(seconds=self.secProcessWindow)
                    if timeProcessing > timeProcessingMax:
                       self._email_cleanup_message(request)
                       bChanged = True

            if bChanged:
                # commit the queue change(s)
                transaction.commit()
        finally:
            mutex.release()

    security.declarePrivate('_email_cleanup_message')
    def _email_cleanup_message(self,request):
        # email techsupport to notify that request has been forcibly been removed from the queue
        mailhost = self.MailHost
        portal = self.portal_url.getPortalObject()
        mfrom = portal.getProperty('email_from_address')
        mto   = portal.getProperty('techsupport_email_address') or mfrom
        if mto:
            subject = "Request '%s' was forcibly removed from processing queue." % request['key']
            messageText = "The request did not successfully complete within the required time window.  The request may have crashed, may be in an infinite loop, or may just be taking a really long time.\n\nThe server and pid referenced in the request need to be checked to see if the process is still active.  If still active, the process may need to be killed.  The QueueTool will no longer consider this an active request.\n\nThe complete request was:\n\n%s\n\n" % str(request)
            mailhost.send(messageText, mto, mfrom, subject)
        self.removeRequest(request['key'])
        self.processingRequests._p_changed = 1
    

    security.declarePublic('ping')
    def ping(self):
        """Test method for ClockServer. Logs when called."""
        self.plone_log("... QueueTool.ping() has been called ...")

    security.declarePublic('clockTick')
    def clockTick(self):
        """
        Called on a clock event every N seconds.  If work is available, a child zopectl process
        is launched.
        """
        # clockTick() acquires mutex lock and is responsible for commiting the transaction.
        mutex.acquire()
        try:
            if self.gotRequest():
                dictRequest = self.getRequest()
                self.launchRequest(dictRequest)
                transaction.commit()

                portal = self.portal_url.getPortalObject()
                portal.plone_log("... QueueTool.clockTick() has been spawned a child process for key '%s' ..." % dictRequest['key'])
        finally:
            mutex.release()

        # removed "expired" request in processing
        self.cleanupProcessing()

    security.declarePrivate('launchRequest')
    def launchRequest(self, dictRequest):
        """ Launch the input request.
        """
        # caller must acquire mutex lock.  caller is repsonsible for commiting the transaction.
        portal = self.portal_url.getPortalObject()
        if dictRequest is not None:
            key = dictRequest['key'] or None
            iListIndex = key is not None and self.find(key, self.processingRequests)
            if iListIndex is not None:
                # request is in the processing queue
                if 'timeRequestProcessed' not in dictRequest:
                    # request has not already been started
                    dictRequest['timeRequestProcessed'] = datetime.now()
                    # dictRequest['ip'] = getipaddr()
                    dictRequest['hostName'] = getfqdn()
                    # dictRequest['pid'] = -1

                    key = dictRequest['key']
                    serverURL = dictRequest.get('serverURL',self.REQUEST['SERVER_URL'])
                    callbackrequesthandler = dictRequest['requestHandler']
                    cmd = os.path.join(INSTANCE_HOME, 'bin', 'zopectl')
                    callback = os.path.join(INSTANCE_HOME, 'Products', callbackrequesthandler)

                    #pid = os.spawnl(os.P_NOWAIT, cmd, 'zopectl', 'run', callback, key)
                    # call newer, better python API which allows for the stdout and stderr to be captured
                    import subprocess
                    f1 = open('/tmp/%s.stdout' % key, 'w')
                    f2 = open('/tmp/%s.stderr' % key, 'w')
                    pid = subprocess.Popen([cmd, "run", callback, key, serverURL], stdout=f1, stderr=f2).pid

                    # child will call start() and stop()

                    dictRequest['pid'] = pid
                    self.processingRequests._p_changed = 1

    security.declareProtected(ManagePermission, 'manage_tick')
    def manage_tick(self):
        """ZMI-useful variant of 'clockTick'. Goes back to overview page of tool.
        From anywhere else, use 'clockTick' instead.
        """
        self.clockTick()
        self.REQUEST.RESPONSE.redirect('manage_overview')
        return

    security.declareProtected(ManagePermission, 'manage_cut_in_line')
    def manage_cut_in_line(self, key=""):
        """Move request to the head of the pendingRequests queue.
        """
        # manage_cut_in_line() acquires mutex lock and is responsible for commiting the transaction.
        mutex.acquire()
        try:
            portal = self.portal_url.getPortalObject()
            iListIndex = self.find(key, self.pendingRequests)
            if iListIndex is not None and iListIndex > 0:
                dictRequest = self.pendingRequests[iListIndex] or None
                if dictRequest is not None:
                    del self.pendingRequests[iListIndex]
                    self.pendingRequests.insert(0, dictRequest)
                    transaction.commit()
                    portal.plone_log("... QueueTool.manage_cut_in_line: '%s' is cutting in line ..." % key)
                    self.REQUEST.RESPONSE.redirect('manage_overview')
        finally:
            mutex.release()
        return

    security.declareProtected(ManagePermission, 'getProcessingRequests')
    def getProcessingRequests(self):
        """Get process requests."""
        return self.processingRequests

    security.declareProtected(ManagePermission, 'getPendingRequests')
    def getPendingRequests(self):
        """Get pending requests."""
        return self.pendingRequests

    security.declarePublic('pending_count')
    def pending_count(self):
        """Return number of pending entries in the queue. Useful for monitoring."""
        return len(self.getPendingRequests())

    security.declarePublic('processing_count')
    def processing_count(self):
        """Return number of pending entries in the queue. Useful for monitoring."""
        return len(self.getProcessingRequests())

#
# Request Callback Example:
#
#import sys
#import transaction
#
#from Products.CMFCore.utils import getToolByName
#
#if len(sys.argv) < 2:
#    print "The script requires one parameter for the request key."
#    sys.exit(0xDEADBEEF)
#
#key = sys.argv[1]
#
#portal= app.objectValues(['Plone Site','CMF Site'])[0]
#qtool = getToolByName(portal, "queue_tool") # app.plone.queue_tool
#dictRequest = qtool.getRequest(key)
#if dictRequest is not None:
#   transaction.commit(); qtool.start(key); transaction.commit()
#   try:
#       import time; time.sleep(10) # faking user specific logic to handle request, with parameters in dictRequest
#   except:
#       pass
#   transaction.commit(); qtool.stop(key); transaction.commit()
#else:
#    print "Could not find the request from the input request key."
#    sys.exit(0xDEADBEEF)
#


InitializeClass(QueueTool)
