import click
from web3 import Web3, EthereumTesterProvider

from validator_set_deploy.core import deploy_validator_set_contracts

from deploy_tools.cli import (
    jsonrpc_option,
    keystore_option,
    gas_option,
    gas_price_option,
    nonce_option,
    auto_nonce_option,
    connect_to_json_rpc,
    retrieve_private_key,
    get_nonce,
)
from deploy_tools.deploy import build_transaction_options

# we need test_provider and test_json_rpc for running the tests in test_cli
# they need to persist between multiple calls to runner.invoke and are
# therefore initialized on the module level.
test_provider = EthereumTesterProvider()
test_json_rpc = Web3(test_provider)


@click.group()
def main():
    pass


@main.command(
    short_help="Deploys the validator set and initializes with the validator addresses."
)
@keystore_option
@gas_option
@gas_price_option
@nonce_option
@auto_nonce_option
@jsonrpc_option
def deploy(
    keystore: str, jsonrpc: str, gas: int, gas_price: int, nonce: int, auto_nonce: bool
) -> None:

    web3 = connect_to_json_rpc(jsonrpc)
    private_key = retrieve_private_key(keystore)

    nonce = get_nonce(
        web3=web3, nonce=nonce, auto_nonce=auto_nonce, private_key=private_key
    )
    transaction_options = build_transaction_options(
        gas=gas, gas_price=gas_price, nonce=nonce
    )

    contracts = deploy_validator_set_contracts(
        web3=web3, transaction_options=transaction_options, private_key=private_key
    )

    click.echo("ValidatorSet address: " + contracts.set.address)
