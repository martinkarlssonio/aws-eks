from aws_cdk import App, Stack, CfnOutput
from aws_cdk import aws_eks as eks
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from constructs import Construct
from aws_cdk.lambda_layer_kubectl_v31 import KubectlV31Layer

import boto3

class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        #################################################
        ################# VARIABLES #####################
        #################################################
        
        region = "eu-north-1"
        account_id = "AccountId"

        #################################################
        ################ ECR CLIENT #####################
        #################################################
        
        ecr_repository_name = "infrastructure-ecr"
        ecr_client = boto3.client("ecr", region_name=region)
        response = ecr_client.describe_repositories()
        ecr_repository_uri = None

        for repo in response["repositories"]:
            if repo["repositoryName"] == ecr_repository_name:
                ecr_repository_uri = repo["repositoryUri"]
                break

        if not ecr_repository_uri:
            raise ValueError(f"ECR repository '{ecr_repository_name}' not found in account {account_id}")

        print(f"ECR Repository URI: {ecr_repository_uri}")


        #################################################
        ################ VPC & NETWORK ##################
        #################################################

        vpc = ec2.Vpc(self, "EksGpuVpc", max_azs=3)

        #################################################
        ###################### IAM  #####################
        #################################################
        
        gpu_node_role = iam.Role(
            self, "GpuNodeRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ]
        )

        cluster_role = iam.Role(
            self, "InfrastructureEksClusterRole",
            role_name="InfrastructureEksClusterRole",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("eks.amazonaws.com"),
                iam.ServicePrincipal("ec2.amazonaws.com")
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSClusterPolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ]
        )

        #################################################
        ################## CLUSTER  #####################
        #################################################
        
        cluster = eks.Cluster(
            self, "Infrastructure",
            vpc=vpc,
            version=eks.KubernetesVersion.V1_31,
            default_capacity=0,  # We manage capacity ourselves
            role=cluster_role,
            kubectl_layer=KubectlV31Layer(self, "KubectlV31Layer")
        )

        gpu_nodegroup = cluster.add_nodegroup_capacity(
            "GpuNodeGroup",
            nodegroup_name="gpu-nodes",
            instance_types=[ec2.InstanceType("g4dn.xlarge")],
            min_size=1,
            max_size=2,
            desired_size=1,
            ami_type=eks.NodegroupAmiType.AL2_X86_64_GPU,
            capacity_type=eks.CapacityType.ON_DEMAND,
            disk_size=250
        )
        ################# K8S METRICS ##################

        metrics_server = cluster.add_manifest(
            "MetricsServer",
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "metrics-server",
                    "namespace": "kube-system",
                    "labels": {"k8s-app": "metrics-server"},
                },
                "spec": {
                    "selector": {"matchLabels": {"k8s-app": "metrics-server"}},
                    "template": {
                        "metadata": {"labels": {"k8s-app": "metrics-server"}},
                        "spec": {
                            "hostNetwork": True,
                            "containers": [
                                {
                                    "name": "metrics-server",
                                    "image": "k8s.gcr.io/metrics-server/metrics-server:v0.6.3",
                                    "args": [
                                        "--cert-dir=/tmp",
                                        "--secure-port=4443",
                                        "--kubelet-insecure-tls",
                                        "--kubelet-preferred-address-types=InternalIP",
                                    ],
                                    "ports": [{"containerPort": 4443, "protocol": "TCP"}],
                                    "securityContext": {"readOnlyRootFilesystem": True, "runAsNonRoot": True},
                                }
                            ],
                        },
                    },
                },
            },
        )

        metrics_service = cluster.add_manifest(
            "MetricsService",
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": "metrics-server",
                    "namespace": "kube-system",
                    "labels": {"k8s-app": "metrics-server"},
                },
                "spec": {
                    "selector": {"k8s-app": "metrics-server"},
                    "ports": [{"protocol": "TCP", "port": 443, "targetPort": 4443}],
                },
            },
        )

        #################### GPU  #######################

        # Deploy NVIDIA Device Plugin using Helm
        # Can be done manually via 'kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.13.0/nvidia-device-plugin.yml'
        
        nvidia_plugin = cluster.add_manifest(
            "NvidiaDevicePluginManifest",
            {
                "apiVersion": "apps/v1",
                "kind": "DaemonSet",
                "metadata": {
                    "name": "nvidia-device-plugin-daemonset",
                    "namespace": "kube-system"
                },
                "spec": {
                    "selector": {"matchLabels": {"name": "nvidia-device-plugin"}},
                    "template": {
                        "metadata": {"labels": {"name": "nvidia-device-plugin"}},
                        "spec": {
                            "containers": [
                                {
                                    "image": "nvcr.io/nvidia/k8s-device-plugin:v0.13.0",
                                    "name": "nvidia-device-plugin-ctr",
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}
                                    },
                                    "volumeMounts": [
                                        {
                                            "name": "device-plugin",
                                            "mountPath": "/var/lib/kubelet/device-plugins"
                                        }
                                    ]
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "device-plugin",
                                    "hostPath": {"path": "/var/lib/kubelet/device-plugins"}
                                }
                            ]
                        }
                    }
                }
            }
        )
        # nvidia_plugin.node.add_dependency(cluster)

        #################################################
        ################ KUEBCTL ADMIN ##################
        #################################################

        cluster.aws_auth.add_user_mapping(
            user=iam.User.from_user_arn(
                self, "CLIUser", f"arn:aws:iam::{account_id}:user/cli-user" # <- Change to your CLI User for Kubectl Admin
            ),
            groups=["system:masters"]
        )

        #################################################
        ################### SRE RESOURCES ###############
        #################################################

        sre_namespace = cluster.add_manifest(
            "SreNamespace",
            {
                "apiVersion": "v1", 
                "kind": "Namespace", 
                "metadata": {
                "name": "sre"
              }
            }
        )

        #################################################
        ################ PORTAL RESOURCES ###############
        #################################################

        portal_namespace = cluster.add_manifest(
            "PortalNamespace",
            {
                "apiVersion": "v1", 
                "kind": "Namespace", 
                "metadata": {
                 "name": "portal"
                }
            }
        )

        portal_deployment = cluster.add_manifest(
            "PortalDeployment",
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "portal", "namespace": "portal"},
                "spec": {
                    "replicas": 1,
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {
                            "maxUnavailable": 1, 
                            "maxSurge": 0
                        }
                    },
                    "selector": {"matchLabels": {"app": "portal"}},
                    "template": {
                        "metadata": {"labels": {"app": "portal"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "portal",
                                    "image": f"{ecr_repository_uri}:portal-ui-v1",
                                    "ports": [{"containerPort": 8000}],
                                    "resources": {
                                        "requests": {"cpu": "500m", "memory": "512Mi"},
                                        "limits": {"cpu": "1000m", "memory": "1Gi"}
                                    }
                                }
                            ]
                        },
                    },
                },
            },
        )

        portal_service = cluster.add_manifest(
            "PortalService",
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "portal", "namespace": "portal"},
                "spec": {
                    "type": "LoadBalancer",
                    "ports": [{"port": 8000, "targetPort": 8000}],
                    "selector": {"app": "portal"},
                },
            },
        )

        portal_deployment.node.add_dependency(portal_namespace)
        portal_service.node.add_dependency(portal_deployment)

        #################################################
        ########### DATAPLATFORM RESOURCES ##############
        #################################################

        dataplatform_namespace = cluster.add_manifest(
            "DataplatformNamespace",
            {
                "apiVersion": "v1", 
                "kind": "Namespace", 
                "metadata": {
                 "name": "dataplatform"
                }
            }
        )

        dataplatform_deployment = cluster.add_manifest(
            "DataplatformDeployment",
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "dataplatform", "namespace": "dataplatform"},
                "spec": {
                    "replicas": 1,
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {
                            "maxUnavailable": 1,
                            "maxSurge": 0
                        }
                    },
                    "selector": {"matchLabels": {"app": "dataplatform"}},
                    "template": {
                        "metadata": {"labels": {"app": "dataplatform"}},
                        "spec": {
                            "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
                            "containers": [
                                {
                                    "name": "dataplatform-ai",
                                    "image": f"{ecr_repository_uri}:dataplatform-genai-v1",
                                    "ports": [{"containerPort": 8010}],
                                    "resources": {
                                        "requests": {"cpu": "3000m", "memory": "8Gi", "nvidia.com/gpu": 1},
                                        "limits": {"cpu": "4000m", "memory": "15Gi", "nvidia.com/gpu": 1}
                                    }
                                }
                            ]
                        },
                    },
                },
            },
        )

        dataplatform_service = cluster.add_manifest(
            "DataplatformService",
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "dataplatform", "namespace": "dataplatform"},
                "spec": {
                    "type": "LoadBalancer",
                    "ports": [{"port": 8010, "targetPort": 8010}],
                    "selector": {"app": "dataplatform"},
                },
            },
        )

        dataplatform_deployment.node.add_dependency(dataplatform_namespace)
        dataplatform_service.node.add_dependency(dataplatform_namespace)


        #################################################
        ################## CDK OUTPUTS ##################
        #################################################

        CfnOutput(self, "ClusterName", value=cluster.cluster_name)

# Create app and stack
app = App()
InfrastructureStack(app, "InfrastructureStack")
app.synth()