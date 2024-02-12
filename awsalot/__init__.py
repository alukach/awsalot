import click
from . import rds_sg_connector, secret_to_pgconn


@click.group()
def cli(): ...


cli.add_command(rds_sg_connector.main, "rds-sg-connector")
cli.add_command(secret_to_pgconn.main, "secret-to-pgconn")
