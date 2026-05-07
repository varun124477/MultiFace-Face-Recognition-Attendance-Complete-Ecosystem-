"""
Run this ONCE to create the Sessions table in DynamoDB.
Usage: python setup_dynamodb.py
"""
import boto3

dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
client   = boto3.client("dynamodb",   region_name="ap-south-1")

def create_table_if_not_exists(name, key):
    existing = client.list_tables()["TableNames"]
    if name in existing:
        print(f"Table '{name}' already exists — skipping.")
        return

    dynamodb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST"
    )
    print(f"✓ Created table: {name}")

create_table_if_not_exists("Students",   "student_id")
create_table_if_not_exists("Attendance", "student_id")
create_table_if_not_exists("Sessions",   "session_id")
create_table_if_not_exists("Teachers",   "teacher_id")

print("\nAll tables ready!")
