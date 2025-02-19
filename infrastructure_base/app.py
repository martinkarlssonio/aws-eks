#!/usr/bin/env python3
import os

import aws_cdk as cdk

from infrastructure_base.infrastructure_base_stack import InfrastructureBaseStack


app = cdk.App()
InfrastructureBaseStack(app, "InfrastructureBaseStack",
    )

app.synth()
