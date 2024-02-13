from typing import List, Dict, Tuple
from getpass import getuser

import boto3
from botocore.exceptions import ClientError
import click
import inquirer


def list_ecs_cluster_arns() -> List[str]:
    ecs = boto3.client("ecs")
    clusters = ecs.list_clusters()
    return clusters["clusterArns"]


def list_ecs_service_arns(cluster) -> List[str]:
    ecs = boto3.client("ecs")
    services = ecs.list_services(cluster=cluster)
    return services["serviceArns"]


def list_rds_instances() -> Dict[str, str]:
    rds = boto3.client("rds")
    return rds.describe_db_instances()["DBInstances"]


def get_security_group_from_ecs_service(cluster, service_arn) -> str:
    ecs = boto3.client("ecs")
    details = ecs.describe_services(cluster=cluster, services=[service_arn])
    service = details["services"][0]
    deployment = service["deployments"][0]
    groups = deployment["networkConfiguration"]["awsvpcConfiguration"]["securityGroups"]
    return groups[0]


def get_security_group_ids_for_rds_instance(instance_identifier) -> List[str]:
    """
    Returns the security group IDs for a given RDS instance identifier.

    :param instance_identifier: The identifier of the RDS instance
    :return: A list of security group IDs associated with the RDS instance
    """
    rds = boto3.client("rds")
    try:
        response = rds.describe_db_instances(DBInstanceIdentifier=instance_identifier)
        db_instances = response["DBInstances"]
        if db_instances:
            # Assuming each instance has at least one security group associated with it
            security_groups = db_instances[0]["VpcSecurityGroups"]
            security_group_ids = [sg["VpcSecurityGroupId"] for sg in security_groups]
            return security_group_ids
        else:
            return []
    except Exception as e:
        print(
            f"Error fetching security group IDs for RDS instance '{instance_identifier}': {e}"
        )
        return []


def modify_security_group_rules(
    security_group_id,
    protocol,
    from_port,
    to_port,
    source_security_group_id: str,
    description: str,
    dry_run: bool,
) -> None:
    ec2 = boto3.client("ec2")
    if dry_run:
        print(
            f"Dry run: Would update Security Group {security_group_id!r} "
            f"to allow connections from {source_security_group_id!r}"
        )
        return

    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {
                    "IpProtocol": protocol,
                    "FromPort": from_port,
                    "ToPort": to_port,
                    "UserIdGroupPairs": [
                        {
                            "GroupId": source_security_group_id,
                            "Description": description,
                        }
                    ],
                }
            ],
        )
        print(f"Security Group {security_group_id} updated successfully.")
    except ClientError as e:
        print(f"Error updating Security Group: {e}")


def get_ecs_security_group() -> str:
    ecs_clusters_arns = list_ecs_cluster_arns()
    default_cluster_arn = next(
        (cluster for cluster in ecs_clusters_arns if "grafana" in cluster), None
    )

    # Select ECS Cluster
    resource_questions = [
        inquirer.List(
            "cluster",
            message="Select ECS Cluster that contains the service that requires databases access",
            choices=ecs_clusters_arns,
            default=default_cluster_arn,
        ),
        inquirer.List(
            "service",
            message="Select ECS Service in {cluster} that requires database access",
            choices=lambda answers: list_ecs_service_arns(answers["cluster"]),
        ),
    ]

    resources = inquirer.prompt(resource_questions)
    return (
        resources["service"].split(":")[-1],
        get_security_group_from_ecs_service(resources["cluster"], resources["service"]),
    )


def get_ec2_security_group() -> Tuple[str, str]:
    ec2 = boto3.client("ec2")

    instances = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )

    choices = []
    # Iterate over all reservations and instances
    for reservation in instances["Reservations"]:
        for instance in reservation["Instances"]:
            name = next(
                tag["Value"] for tag in instance["Tags"] if tag["Key"] == "Name"
            )
            security_group = next(
                sg["GroupId"] for sg in instance.get("SecurityGroups", [])
            )

            choices.append((name, (name, security_group)))

    return inquirer.list_input(
        "Select EC2 Instance that requires database access",
        choices=choices,
    )


@click.command()
@click.option("--dry-run", is_flag=True)
@click.option("--service", type=click.Choice(["ECS", "EC2"], case_sensitive=False))
def main(dry_run, service):
    """
    Edits the security group of selected RDS Instances to allow connectivity from a
    selected ECS Service or EC2 Instance.
    """

    service = service or inquirer.list_input(
        "Select type of service that will be connecting to RDS instances",
        choices=("ecs", "ec2"),
    )

    service_name, src_sg = (
        get_ecs_security_group() if service == "ecs" else get_ec2_security_group()
    )

    # Select ECS Cluster
    resource_questions = [
        inquirer.Checkbox(
            "rds_instances",
            message=f"Select RDS Instances that will be accessed via {service} service",
            choices=[
                instance["DBInstanceIdentifier"] for instance in list_rds_instances()
            ],
        ),
        inquirer.Text(
            "description",
            message="Provide a description for the connection",
            default=lambda answers: f"Allow connections from {service} service {service_name} ({getuser()})",
        ),
    ]

    resources = inquirer.prompt(resource_questions)

    # Get Security Group of selected RDS Instances
    rds_security_groups = [
        get_security_group_ids_for_rds_instance(rds_instance)[0]
        for rds_instance in resources["rds_instances"]
    ]

    # Update RDS Instances' Security Groups to allow inbound connections from ECS Service
    for rds_sg_id in rds_security_groups:
        modify_security_group_rules(
            security_group_id=rds_sg_id,
            source_security_group_id=src_sg,
            protocol="tcp",
            to_port=5432,
            from_port=5432,
            description=resources["description"],
            dry_run=dry_run,
        )
