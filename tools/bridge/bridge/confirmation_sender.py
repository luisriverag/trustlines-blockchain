import logging

import gevent
from eth_keys.datatypes import PrivateKey
from eth_utils import to_checksum_address
from gevent.queue import Queue
from web3.contract import Contract
from web3.datastructures import AttributeDict
from web3.exceptions import TransactionNotFound

from bridge.constants import (
    CONFIRMATION_TRANSACTION_GAS_LIMIT,
    HOME_CHAIN_STEP_DURATION,
)
from bridge.contract_validation import is_bridge_validator
from bridge.utils import compute_transfer_hash

logger = logging.getLogger(__name__)


class ConfirmationSender:
    """Sends confirmTransfer transactions to the home bridge contract."""

    def __init__(
        self,
        transfer_event_queue: Queue,
        home_bridge_contract: Contract,
        private_key: bytes,
        gas_price: int,
        max_reorg_depth: int,
    ):
        self.pending_transaction_queue = Queue()
        w3 = home_bridge_contract.web3

        self.sender = Sender(
            transfer_event_queue=transfer_event_queue,
            home_bridge_contract=home_bridge_contract,
            private_key=private_key,
            gas_price=gas_price,
            max_reorg_depth=max_reorg_depth,
            pending_transaction_queue=self.pending_transaction_queue,
        )
        self.watcher = Watcher(
            w3=w3,
            pending_transaction_queue=self.pending_transaction_queue,
            max_reorg_depth=max_reorg_depth,
        )

    def run(self):
        logger.debug("Starting")
        try:
            greenlets = [
                gevent.spawn(self.watcher.watch_pending_transactions),
                gevent.spawn(self.sender.send_confirmation_transactions),
            ]
            gevent.joinall(greenlets)
        finally:
            for greenlet in greenlets:
                greenlet.kill()


class Sender:
    def __init__(
        self,
        *,
        transfer_event_queue: Queue,
        home_bridge_contract: Contract,
        private_key: bytes,
        gas_price: int,
        max_reorg_depth: int,
        pending_transaction_queue: Queue,
    ):
        self.private_key = private_key
        self.address = PrivateKey(self.private_key).public_key.to_canonical_address()

        if not is_bridge_validator(home_bridge_contract, self.address):
            logger.warning(
                f"The address {to_checksum_address(self.address)} is not a bridge validator to confirm "
                f"transfers on the home bridge contract!"
            )

        self.transfer_event_queue = transfer_event_queue
        self.home_bridge_contract = home_bridge_contract
        self.gas_price = gas_price
        self.max_reorg_depth = max_reorg_depth
        self.w3 = self.home_bridge_contract.web3
        self.pending_transaction_queue = pending_transaction_queue

    def get_next_nonce(self):
        return self.w3.eth.getTransactionCount(self.address, "pending")

    def send_confirmation_transactions(self):
        while True:
            transfer_event = self.transfer_event_queue.get()
            assert isinstance(transfer_event, AttributeDict)

            transaction = self.prepare_confirmation_transaction(transfer_event)
            assert transaction is not None
            self.send_confirmation_transaction(transaction)

    def prepare_confirmation_transaction(self, transfer_event):
        nonce = self.get_next_nonce()

        transfer_hash = compute_transfer_hash(transfer_event)
        transaction_hash = transfer_event.transactionHash
        amount = transfer_event.args.value
        recipient = transfer_event.args["from"]

        logger.info(
            "confirmTransfer(transferHash=%s transactionHash=%s amount=%s recipient=%s) with nonce=%s",
            transfer_hash.hex(),
            transaction_hash.hex(),
            amount,
            recipient,
            nonce,
        )
        # hard code gas limit to avoid executing the transaction (which would fail as the sender
        # address is not defined before signing the transaction, but the contract asserts that
        # it's a validator)
        transaction = self.home_bridge_contract.functions.confirmTransfer(
            transferHash=transfer_hash,
            transactionHash=transaction_hash,
            amount=amount,
            recipient=recipient,
        ).buildTransaction(
            {
                "gasPrice": self.gas_price,
                "nonce": nonce,
                "gas": CONFIRMATION_TRANSACTION_GAS_LIMIT,
            }
        )

        signed_transaction = self.w3.eth.account.sign_transaction(
            transaction, self.private_key
        )

        return signed_transaction

    def send_confirmation_transaction(self, transaction):
        tx_hash = self.w3.eth.sendRawTransaction(transaction.rawTransaction)
        self.pending_transaction_queue.put(transaction)
        logger.info(f"Sent confirmation transaction {tx_hash.hex()}")
        return tx_hash


class Watcher:
    def __init__(self, *, w3, pending_transaction_queue: Queue, max_reorg_depth: int):
        self.w3 = w3
        self.max_reorg_depth = max_reorg_depth
        self.pending_transaction_queue = pending_transaction_queue

    def watch_pending_transactions(self):
        while True:
            self.clear_confirmed_transactions()
            gevent.sleep(HOME_CHAIN_STEP_DURATION)

    def clear_confirmed_transactions(self):
        block_number = self.w3.eth.blockNumber
        confirmation_threshold = block_number - self.max_reorg_depth

        while not self.pending_transaction_queue.empty():
            oldest_pending_transaction = self.pending_transaction_queue.peek()
            try:
                receipt = self.w3.eth.getTransactionReceipt(
                    oldest_pending_transaction.hash
                )
            except TransactionNotFound:
                break

            if receipt and receipt.blockNumber <= confirmation_threshold:
                if receipt.status == 0:
                    logger.warning(
                        f"Transaction failed: {oldest_pending_transaction.hash.hex()}"
                    )
                else:
                    logger.info(
                        f"Transaction has been confirmed: {oldest_pending_transaction.hash.hex()}"
                    )
                confirmed_transaction = (
                    self.pending_transaction_queue.get()
                )  # remove from queue
                assert confirmed_transaction == oldest_pending_transaction
            else:
                break  # no need to look at transactions that are even newer
