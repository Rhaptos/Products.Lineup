"""
QueueTool.py - tool for storing and managing simple event queue

Author: J Cameron Cooper (jccooper@rice.edu) and Brian N West (bnwest@rice.edu)
copyright (C) 2009, Rice University. All rights reserved.

This software is subject to the provisions of the GNU Lesser General
Public License Version 2.1 (LGPL).  See LICENSE.txt for details.
"""

from Products.CMFCore.utils import UniqueObject
from Products.CMFCore.utils import getToolByName
from OFS.SimpleItem import SimpleItem
from Globals import InitializeClass
from ZODB.PersistentList import PersistentList
from Products.PageTemplates.PageTemplateFile import PageTemplateFile

from datetime import datetime, timedelta
from DateTime import DateTime
from socket import getfqdn
import os
import httplib2
import transaction
import AccessControl
from threading import Lock
import simplejson as json

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


TYPE_SPECIFIERS = {
    # type: (suite, format),
    'complete': ('latex', 'completezip'),
    'print': ('princexml', 'pdf'),
    'xml': ('latex', 'offline'),
    }

mutex = Lock()

import zLOG
def log(msg, severity=zLOG.INFO):
    zLOG.LOG("QueueTool: ", severity, msg)

# XXX Modified clone of rbit's rpush.create_job functionality.
#     We are required to clone it because rpush is not backwards
#     compatible and should not be dumbed down to do so.
def create_job(host, port, data, creds=('admin', 'pass')):
    """Creates a job request in PyBit"""
    url = "http://%s:%s/api/job/" % (host, port)
    data = json.dumps(data)
    http = httplib2.Http()
    http.add_credentials(*creds)
    headers = {'Content-type': 'application/json'}
    response, content = http.request(url, 'POST', data, headers=headers)

    if response['status'] != 200:
        raise Exception("Failed to add the package with the following data:"
                        "\ndata: %s"
                        "\nresponse status: %s"
                        "\nresponse body: %s" \
                        % (data, response['status'], content))


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
        self.secProcessWindows  = {}
        self.secDefaultProcessWindow   = self.DEFAULT_PROCESS_TIME_WINDOW
        # unknown to the user
        self.pendingRequests    = PersistentList([])
        self.processingRequests = PersistentList([])
        log('__init__ completed.') # this no workeee???

        # Dependency checkers

    def printtool_dep(self, request, dep):
        """check for the existence of the given filetype instance (objectId, Version, extenstion) newer than the given timestamp"""
        pt = getToolByName(self,'rhaptos_print')
        depkey,deptype,depaction,depdetail = dep
        ptf_params  = (depdetail['objectId'],depdetail['version'],depdetail['extension'])
        if pt.doesFileExist(*ptf_params):
            mod_date  = DateTime(pt.getModificationDate(*ptf_params))
            if mod_date > depdetail['newer']:
                return None
        return dep

    def url_dep(self, request, dep):
        """check for bits available from the given url, with a given mimetype and newer than the given timestamp"""
        pass

    def file_dep(self, request, dep):
        """check for the existence if a file at the given path, that is newer than the given timestamp"""
        depkey,deptype,depaction,depdetail = dep
        try:
            fstat = os.stat(depdetail['path'])
        except OSError:
            return dep
        if DateTime(fstat.st_ctime) > depdetail['newer']:
            return None
        return dep


    security.declareProtected(ManagePermission, 'manage_queue_tool')
    def manage_queue_tool(self, pybitHostname='', pybitPort=8080,
                          pybitUsername='admin', pybitPassword='pass'):
        """
        Post creation configuration.  See manage_configure_queue_tool.zpt
        """
        self.pybitHostname = pybitHostname
        self.pybitPort = int(pybitPort)
        self.pybitUsername = pybitUsername
        self.pybitPassword = pybitPassword

    security.declareProtected(ManagePermission, 'add')
    def add(self, key, dictParams, callbackrequesthandler, priority=1):
        """
        Add a request to the Pending Queue.
        """
        project = getattr(dictParams, 'project_name', 'cnx')
        # Types of builds coming in are: colcomplete, colprint, colxml
        type_specifier = key.split('_', 1)[0].lstrip('col')

        # add() acquires mutex lock.
        # caller is responsible for commiting the transaction.
        mutex.acquire()
        try:
            suite, format = TYPE_SPECIFIERS[type_specifier]

            data = {'package': dictParams['id'],
                    'version': dictParams['version'],
                    'uri': dictParams['serverURL'],
                    'arch': 'any',
                    'dist': project,
                    'suite': suite,
                    'format': format,
                    }
            host = getattr(self, 'pybitHostname', 'localhost')
            port = getattr(self, 'pybitPort', 8091)
            username = getattr(self, 'pybitUsername', 'admin')
            password = getattr(self, 'pybitPassword', 'pass')
            creds = (username, password)
            create_job(host, port, data, creds)

            # FIXME Removed due to inability to prioritize in a linear
            #       (non-topic based) message queue implemenation.
            # Walk list, insert immediately before first entry that is
            #   lower priority (higher value)
            ##dictRequest['priority'] = priority
            ##for i,req in enumerate(self.pendingRequests):
            ##    if req['priority'] > priority:
            ##        self.pendingRequests.insert(i,req)
            ##        break
            ##else:
            ##    # didn't find one, tack on the end
            ##    self.pendingRequests.append(dictRequest)

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
        Determine if any requests are ready for processing. Return key of first one, if any, else False.
        Does dependency checking
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

        portal = self.portal_url.getPortalObject()
        # check requests for dependencies, bail out at first success
        for dictRequest in self.pendingRequests:
            key = dictRequest['key']
            unmetDepends = self.checkDepends(dictRequest)
            if unmetDepends:
                if unmetDepends[2] == 'reenqueue':
                    iListIndex = self.find(key, self.pendingRequests)
                    portal.plone_log("... reenqueue request for key '%s'at index '%s' ..." % (dictRequest['key'],iListIndex))
                    if iListIndex is not None:
                        portal.plone_log("... QueueTool.clockTick() has skipped request for key '%s' for failed dependency '%s' ..." % (dictRequest['key'],str(unmetDepends)))
                elif unmetDepends[2] == 'fail':
                    self._email_depend_fail(dictRequest,unmetDepends) # removes req from pending as well
                else:
                    portal.plone_log("... unknown failed dependency action for key '%s' ... skipping." % (dictRequest['key'],))
            else:
                return key

        # fell through loop: no requests w/o unmet dependencies
        return False

    security.declarePrivate('getRequest')
    def getRequest(self, key=None):
        """
        Get a request from the pending queue and put it in the processing queue or
        get an request already in the processing queue via key.
        """
        # caller must acquire mutex lock.  caller is responsible for commiting the transaction.
        if key is None:
            # get a request from the pending queue and put it in the processing queue
            reqKey = self.gotRequest()
            if reqKey:
                iListIndex = self.find(reqKey, self.pendingRequests)
                if iListIndex is not None:
                    dictRequest = self.pendingRequests[iListIndex]
                    del self.pendingRequests[iListIndex]
                    self.processingRequests.append(dictRequest)
                    return dictRequest
                else:
                    return None
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
        # start() acquires mutex lock and is responsible for commiting the transaction.
        return


    security.declarePrivate('stop')
    def stop(self, key):
        """
        Stop processing the queued request.
        """
        # stop() acquires mutex lock and is responsible for commiting the transaction.
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
    def removeRequest(self, key, fromList=None):
        """
        Remove a request from the processing queue.
        """
        # caller must acquire mutex lock.  caller is responsible for commiting the transaction.
        if not fromList:
            fromList = self.processingRequests
        iListIndex = self.find(key, fromList)
        if iListIndex is not None:
            del fromList[iListIndex]


    security.declarePrivate('cleanupProcessing')
    def cleanupProcessing(self):
        """
        Cleanup the processingRequests queue.  Assume that processing entries that
        do not complete in a defined time window cored and did not call removeRequest().
        """
        # cleanupProcessing() acquires mutex lock and is responsible for commiting the transaction.
        mutex.acquire()
        try:
            timeNow = datetime.now()
            bChanged = False
            for request in self.processingRequests:
                timeProcessStarted = 'timeRequestProcessed' in request and request['timeRequestProcessed'] or None
                childPid = request.get('pid')
                if childPid:
                    try:
                        os.kill(childPid, 0)
                    except OSError:
                        if timeProcessStarted is not None:
                            self._email_cleanup_message(request,0)
                            bChanged = True

                if timeProcessStarted is not None:
                    timeProcessing = timeNow - timeProcessStarted
                    timeProcessingMax = timedelta(seconds=self._getProcessingMax(request))
                    if timeProcessing > timeProcessingMax:
                       self._email_cleanup_message(request,timeProcessingMax)
                       bChanged = True

            if bChanged:
                # commit the queue change(s)
                transaction.commit()
        finally:
            mutex.release()


    security.declarePrivate('_get_ProcessingMax')
    def _getProcessingMax(self,request):
        # Get process max timeout, checking for exact queue key, keyclass, and default, in that order.
        key = request['key']
        key_class = key.split('_')[0]
        windows = self.secProcessWindows
        return windows.get(key, windows.get(key_class, self.secDefaultProcessWindow))

    security.declarePrivate('_email_cleanup_message')
    def _email_cleanup_message(self,request,timeout):
        # email techsupport to notify that request has been forcibly been removed from the queue
        mailhost = self.MailHost
        portal = self.portal_url.getPortalObject()
        mfrom = portal.getProperty('email_from_address')
        mto   = portal.getProperty('techsupport_email_address') or mfrom
        if mto:
            subject = "Request '%s' was forcibly removed from processing queue." % request['key']
            messageText = "The request did not successfully complete within the required time window of %s.  The request may have crashed, may be in an infinite loop, or may just be taking a really long time.\n\nThe server and pid referenced in the request need to be checked to see if the process is still active.  If still active, the process may need to be killed.  The QueueTool will no longer consider this an active request.\n\nThe complete request was:\n\n%s\n\n" % (timeout,str(request))
            self._mailhost_send(messageText, mto, mfrom, subject)

        self.removeRequest(request['key'])
        self.processingRequests._p_changed = 1

    security.declarePrivate('_mailhost_send')
    def _mailhost_send(self, messageText, mto, mfrom, subject):
        """attempt to send mail: log if fail"""
        portal = self.portal_url.getPortalObject()
        try:
            mailhost.send(messageText, mto, mfrom, subject)
        except:
            portal.plone_log("Can't send email:\n\nSubject:%s\n\n%s" % (subject,messageText))

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
        portal = self.portal_url.getPortalObject()
        try:
            if self.gotRequest():
                dictRequest = self.getRequest()
                if dictRequest:
                    transaction.commit() # make list change visible to child instance - other way was race condition
                    self.launchRequest(dictRequest)
                    portal.plone_log("... QueueTool.clockTick() has spawned a child process for key '%s' ..." % dictRequest['key'])

        finally:
            mutex.release()

        # removed "expired" request in processing
        self.cleanupProcessing()

    security.declarePublic('checkDepends')
    def checkDepends(self, request):
        """Check the request dependecies, in declared order. Return first unmet
        one. On success of all (or no declared dependencies) return 'None'"""
        for dep in request.get('depends',[]):
            try:
                checker = getattr(self,"%s_dep" % dep[1])
                if checker(request,dep):
                    return dep
            except AttributeError:
                self.portal_url.getPortalObject().plone_log('Skipping badly declared dependency: %s' % (dep,))
                pass

        return None

    security.declarePrivate('_email_depend_fail')
    def _email_depend_fail(self,request,depend):
        # email techsupport to notify that request has been forcibly been removed from the queue
        mailhost = self.MailHost
        portal = self.portal_url.getPortalObject()
        mfrom = portal.getProperty('email_from_address')
        mto   = portal.getProperty('techsupport_email_address') or mfrom
        if mto:
            subject = "Request '%s' was removed from pending queue. (dependency)" % request['key']
            messageText = "The request declared a dependency that was not available at the time of processing.  The QueueTool will no longer consider this an active request.\n\nThe failed dependency was:\n\n%s\n\nThe complete request was:\n\n%s\n\n" % (str(depend),str(request))
            self._mailhost_send(messageText, mto, mfrom, subject)
        self.removeRequest(request['key'], self.pendingRequests)
        self.pendingRequests._p_changed = 1

    security.declarePrivate('launchRequest')
    def launchRequest(self, dictRequest):
        """ Launch the input request.
        """
        # caller must acquire mutex lock.  caller is responsible for commiting the transaction.
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

    security.declareProtected(ManagePermission, 'manage_remove')
    def manage_remove(self, key="", queue="pending"):
        """Remove request from a queue list
        """
        # manage_cut_in_line() acquires mutex lock and is responsible for commiting the transaction.
        mutex.acquire()
        if queue == "pending":
            fromList = self.pendingRequests
        elif queue == "processing":
            fromList = self.processingRequests
        else:
            return
        try:
            self.removeRequest(key,fromList)
            portal = self.portal_url.getPortalObject()
            portal.plone_log("... QueueTool.manage_remove: '%s' has been removed ..." % key)
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
