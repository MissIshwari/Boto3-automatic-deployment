import boto3
import botocore
import paramiko

#create a bucket
#upload files for Travel Memory application

s3_client=boto3.client('s3')
response=s3_client.create_bucket(Bucket='travel-memory',
        CreateBucketConfiguration={
        'LocationConstraint': 'ap-south-1'
    })

backend_folder="C:/Users/ishwa/TravelMemory/backend.zip"

frontend_folder="C:/Users/ishwa/TravelMemory/frontend.zip"

nginx_default_file="C:/Users/ishwa/TravelMemory/default"

#Uploading files to S3 bucket
response=s3_client.upload_file(backend_folder, 'travel-memory',"backend.zip")
response=s3_client.upload_file(frontend_folder, 'travel-memory',"frontend.zip")
response=s3_client.upload_file(nginx_default_file, 'travel-memory',"default")
s3_client=boto3.client('s3')

#Creating EC2

ec2_client=boto3.client('ec2')

user_data_script="""#!/bin/bash
sudo apt update
sudo apt install nodejs
sudo apt install nginx -y
"""
ImageId="ami-03f4878755434977f"

response=ec2_client.run_instances(ImageId=ImageId,InstanceType='t2.micro',KeyName='ishwari',
UserData=user_data_script,
MaxCount=1,MinCount=1,SecurityGroupIds=['sg-0aacef159b7cae6e8'],TagSpecifications=[
        {
            'ResourceType': 'instance',
            'Tags': [
                {
                    'Key': 'Name',
                    'Value': 'TravelMemory',
                },
            ],
        },
    ])

instance_id1 = response['Instances'][0]['InstanceId']

response=ec2_client.run_instances(ImageId=ImageId,InstanceType='t2.micro',KeyName='ishwari',
UserData=user_data_script,
MaxCount=1,MinCount=1,SecurityGroupIds=['sg-0aacef159b7cae6e8'],TagSpecifications=[
        {
            'ResourceType': 'instance',
            'Tags': [
                {
                    'Key': 'Name',
                    'Value': 'TravelMemory',
                },
            ],
        },
    ])
instance_id2=response['Instances'][0]['InstanceId']

waiter = ec2_client.get_waiter('instance_running')
waiter.wait(InstanceIds=[instance_id1,instance_id2])


#Asociating instance profile arn to EC2 to pull S3 bucket objects
response = ec2_client.associate_iam_instance_profile(
    IamInstanceProfile={
        'Arn': 'arn:aws:iam::767397796297:instance-profile/EC2-S3-Read',
        'Name': 'EC2-S3-Read'
    },
    InstanceId=instance_id1
)

response = ec2_client.associate_iam_instance_profile(
    IamInstanceProfile={
        'Arn': 'arn:aws:iam::767397796297:instance-profile/EC2-S3-Read',
        'Name': 'EC2-S3-Read'
    },
    InstanceId=instance_id2
)

#SSH into EC2 and pull in the s3 objects of application and run them instantaneously

key=paramiko.RSAKey.from_private_key_file('../../Downloads/ishwari-personal.pem')
client=paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

client.connect(hostname=instance_ip,username='ubuntu',pkey=key)
cmd='''
aws s3 cp s3://travel-memory/backend.zip .
aws s3 cp s3://travel-memory/frontend.zip .
aws s3 cp s3://travel-memory/default .
unzip backend.zip
unzip frontend.zip
cd backend
npm install
node index.js &
cd ..
cd frontend
npm install
npm start &
cd ..
sudo cp default /etc/nginx/sites-enabled
sudo systemctl restart nginx
'''
client.exec_command(cmd)
client.close()

elbv2_client=boto3.client('elbv2')
# Create an Application Load Balancer (ALB) to balance load between the 2 targets
response=elbv2_client.create_load_balancer(
    Name='travel-memory-load-balancer',
    Subnets=['subnet-0d252a8b46752a3cb','subnet-0f19585981ee5cbed'],
    SecurityGroups=[
        'sg-08c90de5a0006db18',
    ],
    Scheme='internet-facing',
    Type='application',
    IpAddressType='ipv4',
    Tags=[{'Key':'Name','Value':'travel-memory-load-balancer'}]
)

# Registering EC2 instances to Application Load Balancer

response=elbv2_client.register_targets(
    TargetGroupArn='',
    Targets=[
        {
            'Id': instance_id1,
            'Port': 3000,
            'AvailabilityZone': 'us-east-1'
        },
        {
            'Id':instance_id2,
            'Port':3000,
            'AvailabilityZone':'us-east-2'
        }
    ]
)

autoscaling_client = boto3.client('autoscaling')
#Auto scaling group creation with launch template


response = autoscaling_client.create_auto_scaling_group(
    AutoScalingGroupName='asg-tm',
    LaunchConfigurationName='configuration-asg',
    LaunchTemplate={
        'LaunchTemplateId': 'lt-0bc6ec04db94ccde0',
        'LaunchTemplateName': 'scaling-launch-template',
        'Version': '1'
    },
    MinSize=1,
    MaxSize=5,
    DesiredCapacity=2,
    DefaultCooldown=123,
    AvailabilityZones=[
        'ap-south-1a',
        'ap-south-1b',
        'ap-south-1c'
    ],
    LoadBalancerNames=[
        'travel-memory-load-balancer'
    ],
    TargetGroupARNs=[
        'arn:aws:elasticloadbalancing:ap-south-1:767397796297:targetgroup/target-group-tm/76e935d36558cb71',
    ],
    HealthCheckType='EC2 health checks',
    HealthCheckGracePeriod=300,
    DesiredCapacityType='Units(number of instances)'
)

response=autoscaling_client.put_scaling_policy(
    AutoScalingGroupName='web-app-asg',
    PolicyName='cpu-utilization-scaling-policy',
    PolicyType='TargetTrackingScaling',
    TargetTrackingConfiguration={
        'TargetValue': 70.0,
        'PredefinedMetricSpecification': {
            'PredefinedMetricType': 'ASGAverageCPUUtilization'
        }
    }
)
    
elbv2_client = boto3.client('elbv2')
ec2_client = boto3.client('ec2')
asg_client = boto3.client('autoscaling')
sns_client = boto3.client('sns')

#Lambda function to terminate instance and send sns notifications

def lambda_handler(event, context):
    
    alb_arn = 'arn:aws:elasticloadbalancing:ap-south-1:767397796297:loadbalancer/app/travel-memory-load-balancer/f573845f5d304cbd'
    target_group_arn = 'arn:aws:elasticloadbalancing:ap-south-1:767397796297:targetgroup/target-group-tm/76e935d36558cb71'

    # Check the health of targets in the specified target group
    response = elbv2_client.describe_target_health(
        TargetGroupArn=target_group_arn
    )

    # Iterate through each target health description
    for target_health in response['TargetHealthDescriptions']:
        target_id = target_health['Target']['Id']
        target_health_state = target_health['TargetHealth']['State']

        # If the target health is not 'healthy', take necessary actions
        if target_health_state != 'healthy':
            print(f"Target {target_id} is not healthy.")

            # Terminate the problematic instance
            terminate_instance(target_id)

            # Send notification through SNS to administrators
            send_notification(target_id)


def terminate_instance(instance_id):
    # Terminate the problematic instance
    ec2_client.terminate_instances(InstanceIds=[instance_id])
    print(f"Instance {instance_id} terminated.")

s3_client = boto3.client('s3')
sns_client = boto3.client('sns')

# Analyze ALB access logs
def analyze_access_logs(bucket, key):
    # Downloading and extracting log file
    log_obj = s3_client.get_object(Bucket=bucket, Key=key)
    log_data = gzip.decompress(log_obj['Body'].read()).decode('utf-8')

    # Check for high traffic based on logs
    log_entries = log_data.split('\n')
    if len(log_entries) > 1000:
        send_notification("High traffic detected in ALB access logs")


# Function to send notification via SNS
def send_sns_notification(message):
    sns_topic_arn = 'arn:aws:sns:us-east-1:123456789012:tm-failure'
    sns_client.publish(
        TopicArn=sns_topic_arn,
        Message=message,
        Subject="ALB Log Analysis Alert"
    )

















