import cv2
import numpy as np
import boto3
from insightface.app import FaceAnalysis

# ---------- AWS ----------
dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
table = dynamodb.Table("Students")

# ---------- Load model ----------
app = FaceAnalysis(name="buffalo_l")
app.prepare(ctx_id=0, det_size=(640,640))

# ---------- Load students from DynamoDB ----------
response = table.scan()

for item in response["Items"]:

    image_key = item.get("image_key")

    if not image_key:
        continue

    path = image_key

    print("Processing:", path)

    img = cv2.imread(path)

    if img is None:
        print("FAILED to load image:", path)
        continue

    faces = app.get(img)

    if len(faces) == 0:
        print("No face found:", path)
        continue

    embedding = faces[0].embedding.tolist()

    table.update_item(
        Key={"student_id": item["student_id"]},
        UpdateExpression="SET embedding = :e",
        ExpressionAttributeValues={
            ":e": [float(x) for x in embedding]
        }
    )

    print("Embedding saved for:", item["name"])

print("DONE")
 
