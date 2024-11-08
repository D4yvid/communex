from dataclasses import dataclass
from getpass import getpass
from typing import Any, Mapping, cast, TypeVar

import rich
import typer
from rich import box
from rich.console import Console
from rich.table import Table
from typer import Context

from communex._common import get_node_url
from communex.balance import from_horus, from_nano, dict_from_nano
from communex.client import CommuneClient
from communex.types import (
    ModuleInfoWithOptionalBalance,
    NetworkParams,
    SubnetParamsWithEmission,
    )


@dataclass
class ExtraCtxData:
    output_json: bool
    use_testnet: bool
    yes_to_all: bool

    interactive: bool
    color: bool

class ExtendedContext(Context):
    obj: ExtraCtxData

@dataclass
class CustomCtx:
    ctx: ExtendedContext
    console: rich.console.Console
    console_err: rich.console.Console
    _com_client: CommuneClient | None = None

    use_json_output: bool = False
    use_yes_to_all: bool = False
    use_testnet: bool = False

    interactive: bool = True
    color: bool = True

    def __init__(
        self,
        ctx: ExtendedContext
    ):
        self.ctx = ctx

        self.use_json_output = ctx.obj.output_json
        self.use_yes_to_all = ctx.obj.yes_to_all
        self.use_testnet = ctx.obj.use_testnet
        self.interactive = ctx.obj.interactive
        self.color = ctx.obj.color

        self.console = Console(no_color = not self.color)
        self.console_err = Console(stderr = True, no_color = not self.color)

    def com_client(self) -> CommuneClient:
        use_testnet = self.use_testnet

        if self._com_client is not None:
            return self._com_client

        node_url = get_node_url(None, use_testnet = use_testnet)

        with self.progress_status() as status:
            for current in range(1, 6):
                status.update(f'Connecting to node {node_url}...')

                try:
                    self._com_client = CommuneClient(
                        url = node_url, num_connections = 1, wait_for_finalization = False)

                    # If the code is here, it connected successfully
                    break
                except Exception:
                    status.update(f'Connecting to node {node_url} ({current}/5 retries)...')

        if self._com_client is None:
            raise ConnectionError("Could not connect to any node")

        return self._com_client

    def get_use_testnet(self) -> bool:
        return self.ctx.obj.use_testnet

    def output(
        self,
        message: str,
        *args: tuple[Any, ...],
        **kwargs: dict[str, Any],
    ) -> None:
        self.console.print(message, *args, **kwargs)  # type: ignore

    def info(
        self,
        message: str,
        *args: tuple[Any, ...],
        **kwargs: dict[str, Any],
    ) -> None:
        self.console_err.print(message, *args, **kwargs)  # type: ignore

    def error(self, message: str) -> None:
        message = f"ERROR: {message}"
        self.console_err.print(message, style="bold red")

    def progress_status(self, message: str = ''):
        return self.console_err.status(message)

    def confirm(self, message: str) -> bool:
        if (self.ctx.obj.yes_to_all):
            print(f"{message} (--yes)")
            return True
        return typer.confirm(message)


def make_custom_context(ctx: typer.Context) -> CustomCtx:
    return CustomCtx(
        ctx = cast(ExtendedContext, ctx)
    )


# Formatting


def eprint(e: Any) -> None:
    """
    Pretty prints an error.
    """

    console = Console()

    console.print(f"[bold red]ERROR: {e}", style="italic")


def print_table_from_plain_dict(
    result: Mapping[str, str | int | float | dict[Any, Any]], column_names: list[str], console: Console
) -> None:
    """
    Creates a table for a plain dictionary.
    """

    table = Table(show_header=True, header_style="bold magenta")

    for name in column_names:
        table.add_column(name, style="white", vertical="middle")

    # Add non-dictionary values to the table first
    for key, value in result.items():
        if not isinstance(value, dict):
            table.add_row(key, str(value))
    # Add subtables for nested dictionaries.
    # Important to add after so that the display of the table is nicer.
    for key, value in result.items():
        if isinstance(value, dict):
            subtable = Table(show_header=False, padding=(0, 0, 0, 0), border_style="bright_black")
            for subkey, subvalue in value.items():
                subtable.add_row(f"{subkey}: {subvalue}")
            table.add_row(key, subtable)

    console.print(table)



def print_table_standardize(result: dict[str, list[Any]], console: Console) -> None:
    """
    Creates a table for a standardized dictionary.
    """
    table = Table(show_header=True, header_style="bold magenta")

    for key in result.keys():
        table.add_column(key, style="white")
    rows = [*result.values()]
    zipped_rows = [list(column) for column in zip(*rows)]
    for row in zipped_rows:
        table.add_row(*row, style="white")

    console.print(table)


def transform_module_into(
    to_exclude: list[str], last_block: int,
    immunity_period: int, modules: list[ModuleInfoWithOptionalBalance],
    tempo: int
):
    mods = cast(list[dict[str, Any]], modules)
    transformed_modules: list[dict[str, Any]] = []
    for mod in mods:
        module = mod.copy()
        module_regblock = module["regblock"]
        module["in_immunity"] = module_regblock + immunity_period > last_block

        for key in to_exclude:
            del module[key]
        module["stake"] = round(from_nano(module["stake"]), 2)
        module["emission"] = round(
            from_horus(
                module["emission"], tempo
            ),
            4
        )
        if module.get("balance") is not None:
            module["balance"] = from_nano(module["balance"])
        else:
            # user should not see None values
            del module["balance"]
        transformed_modules.append(module)

    return transformed_modules


def print_module_info(
        client: CommuneClient,
        modules: list[ModuleInfoWithOptionalBalance],
        console: Console,
        netuid: int,
        title: str | None = None,
) -> None:
    """
    Prints information about a module.
    """
    if not modules:
        return

    # Get the current block number, we will need this to caluclate immunity period
    block = client.get_block()
    if block:
        last_block = block["header"]["number"]
    else:
        raise ValueError("Could not get block info")

    # Get the immunity period on the netuid
    immunity_period = client.get_immunity_period(netuid)
    tempo = client.get_tempo(netuid)

    # Transform the module dictionary to have immunity_period
    table = Table(
        show_header=True, header_style="bold magenta",
        box=box.DOUBLE_EDGE, title=title,
        caption_style="chartreuse3",
        title_style="bold magenta",

    )

    to_exclude = ["stake_from", "last_update", "regblock"]
    tranformed_modules = transform_module_into(
        to_exclude, last_block, immunity_period, modules, tempo
    )

    sample_mod = tranformed_modules[0]
    for key in sample_mod.keys():
        # add columns
        table.add_column(key, style="white")

    total_stake = 0
    total_balance = 0

    for mod in tranformed_modules:
        total_stake += mod["stake"]
        if mod.get("balance") is not None:
            total_balance += mod["balance"]

        row: list[str] = []
        for val in mod.values():
            row.append(str(val))
        table.add_row(*row)

    table.caption = "total balance: " + f"{total_balance + total_stake}J"
    console.print(table)
    for _ in range(3):
        console.print()


def get_universal_password(ctx: CustomCtx) -> str:
    ctx.info("Please provide the universal password for all keys")
    universal_password = getpass()
    return universal_password


def tranform_network_params(params: NetworkParams):
    """Transform network params to be human readable."""
    governance_config = params["governance_config"]
    allocation = governance_config["proposal_reward_treasury_allocation"]
    governance_config = cast(dict[str, Any], governance_config)
    governance_config["proposal_reward_treasury_allocation"] = f"{allocation}%"
    params_ = cast(dict[str, Any], params)
    params_["governance_config"] = governance_config
    general_params = dict_from_nano(params_, [
        "min_weight_stake",
        "general_subnet_application_cost",
        "subnet_registration_cost",
        "proposal_cost",
        "max_proposal_reward_treasury_allocation",
    ])

    return general_params


T = TypeVar("T")
V = TypeVar("V")
def remove_none_values(data: dict[T, V | None]) -> dict[T, V]:
    """
    Removes key-value pairs from a dictionary where the value is None.
    Works recursively for nested dictionaries.
    """
    cleaned_data: dict[T, V] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            cleaned_value = remove_none_values(value) # type: ignore
            if cleaned_value is not None: # type: ignore
                cleaned_data[key] = cleaned_value
        elif value is not None:
            cleaned_data[key] = value
    return cleaned_data



def transform_subnet_params(params: SubnetParamsWithEmission):
    """Transform subnet params to be human readable."""
    params_ = cast(dict[str, Any], params)
    display_params = remove_none_values(params_)
    display_params = dict_from_nano(
        display_params, [
            "bonds_ma",
            "min_burn",
            "max_burn",
            "min_weight_stake",
            "proposal_cost",
            "max_proposal_reward_treasury_allocation",
        ]
    )
    return display_params
