#!/usr/bin/env python3
import aws_cdk as cdk
from infrastructure.infrastructure_stack import InfrastructureStack

app = cdk.App()
InfrastructureStack(
    app, "InfrastructureStack",
    env=cdk.Environment(account="AccountID", region="eu-north-1")
)

app.synth()