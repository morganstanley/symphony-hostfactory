"""Command line helpers"""

from typing import Union

import click


def output(value: Union[str, float, bool]) -> None:
    """Output value"""
    return click.echo(value)
