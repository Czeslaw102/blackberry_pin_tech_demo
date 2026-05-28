import logging
from threading import Thread, Condition
from pim.objects.orm import Message
from pim.objects.providers.message import MessageStatusEnum

logger = logging.getLogger('pin.provider.statuslistener')

SEND_RESULT_SUCCESS = 0
SEND_RESULT_PERMANENT_FAILURE = 1
SEND_RESULT_TEMPORARY_FAILURE = 2
SEND_RESULT_SUCCESS_ACCEPTED = 5
SEND_RESULT_SUCCESS_DELIVERED = 6

class PINMessageStatusListener(Thread):
    def __init__(self, pinProvider):
        self.pinProvider = pinProvider
        self.shutdownRequested = False
        self.queueCondition = Condition()
        self.messageStatusQueue = []
        super().__init__()

    def run(self):
        logger.info('Starting PIN Messaging Status Listener')
        result = self.handle_status_updates()
        logger.info('PIN Messaging Status Listener exited, result was {}'.format(result))

    def shutdown(self):
        if self.isAlive():
            logger.error('The PIN Messaging Status Listener is currently running.')
        with self.queueCondition:
            self.shutdownRequested = True
            self.queueCondition.notify()
        logger.debug('PIN Messaging Status Listener, has been notified to shutdown, joining to wait...')
        self.join(5)
        if self.isAlive():
            logger.error('The PIN Messaging Status Listener is still running.')
        logger.info('Completed shutdown of the PIN Messaging Status Listener')

    def add_status_update(self, refid, status):
        logger.debug('In process_status_update.  refid=%d, status=%d', refid, status)
        try:
            statusUpdate = {}
            statusUpdate['refid'] = refid
            statusUpdate['status'] = status
            with self.queueCondition:
                self.messageStatusQueue.append(statusUpdate)
                self.queueCondition.notify()
        except:
            logger.exception('Exception: pin_message_send_status_update')

    def handle_status_updates(self):
        try:
            with self.queueCondition:
                while not self.shutdownRequested:
                    if len(self.messageStatusQueue) == 0:
                        logger.debug('handle_status_updates: waiting status changes.....')
                        self.queueCondition.wait()
                        logger.debug('handle_status_updates: woke ')
                    logger.debug('PIN Message Status Listener, there are status updates pending...')
                    if len(self.messageStatusQueue) > 0:
                        statusUpdate = self.messageStatusQueue.pop()
                        if statusUpdate:
                            self.update_message_status(statusUpdate['refid'], statusUpdate['status'])
                logger.info('Stopping Listening for status changes...')
        except:
            logger.exception('handle_status_updates')

    def update_message_status(self, refid, status):
        newSession = self.pinProvider.provider_session.get_session()
        logger.info('PIN Message with refid [%d] has a send status of [%d]', refid, status)
        try:
            msg = newSession.query(Message).filter_by(sync_id=refid).first()
            if msg is None:
                logger.warning('PIN Message with refid %d could not be found!', refid)
                return
            if status == SEND_RESULT_SUCCESS:
                logger.debug('Message was sent to relay, with no failures....')
                msg.status = MessageStatusEnum.SENDING
            elif status == SEND_RESULT_SUCCESS_DELIVERED:
                logger.info('Message delivered.')
                msg.status = MessageStatusEnum.SENT
                msg.folder_id = self.pinProvider.pinSentFolderId
            elif status == SEND_RESULT_PERMANENT_FAILURE:
                logger.info('Message encountered a permanent send failure, no retries will be attempted.')
                msg.status = MessageStatusEnum.FAILED_TO_SEND
                msg.folder_id = self.pinProvider.pinDraftsFolderId
            elif status == SEND_RESULT_TEMPORARY_FAILURE:
                logger.info('Message encountered a temporary send failure, it will be retried.')
                msg.status = MessageStatusEnum.PENDING_WITH_ERRORS
            elif status == SEND_RESULT_SUCCESS_ACCEPTED:
                logger.debug('Message was accepted by relay, wait for delivery or failures.')
                msg.status = MessageStatusEnum.ACCEPTED
            else:
                logger.error('Unknown states type %d for refid %d', status, refid)
            try:
                msg.status_description = ''
            except:
                pass
            try:
                msg.sync_dirty = False
            except:
                pass
            newSession.add(msg)
            newSession.commit()
            self.pinProvider.notify.notify_message_changed(msg.id, msg.conversation_id, msg.folder_id, changes={'status': msg.status, 'folder_id': msg.folder_id}, from_UI=False)
        except:
            logger.exception('update_message_status')
