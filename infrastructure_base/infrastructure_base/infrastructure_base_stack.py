from aws_cdk import Stack, CfnOutput
from aws_cdk import aws_ecr as ecr
from constructs import Construct

class InfrastructureBaseStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Container Registry (ECR)
        ecr_repository = ecr.Repository(self, "InfrastructureEcrRepository",
                                        repository_name="infrastructure-ecr",
                                        lifecycle_rules=[
                                            ecr.LifecycleRule(
                                                description="Keep only the last 10 images",
                                                max_image_count=10
                                            )
                                        ])
        # Outputs
        CfnOutput(self, "EcrRepositoryUri", value=ecr_repository.repository_uri, description="ECR Repository URI")