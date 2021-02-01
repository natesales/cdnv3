from os import environ
from time import time
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, WebSocket, Response, status
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from rich.console import Console

from models import eca as eca_models
from models.dns import *
from validation import *

if environ.get("CDNV3_DEVELOPMENT"):
    DEVELOPMENT = True
else:
    DEVELOPMENT = False

# Rich console
console = Console()

app = FastAPI(title="Packetframe Control Plane", description="Control plane for the Packetframe CDN", version="3.0.0")

console.log("Connecting to MongoDB")
if DEVELOPMENT:
    console.log("Using local development database")
    db = MongoClient("localhost:27017")["cdnv3db"]
else:  # Production replicaset
    db = MongoClient("localhost:27017", replicaSet="cdnv3")["cdnv3db"]
console.log("Connected to MongoDB")
db["zones"].create_index([("zone", ASCENDING)], unique=True)


@app.post("/ecas/new")
async def new_eca(eca: eca_models.ECA):
    eca_dict = eca.dict()
    eca_dict["authorized"] = False
    _id = db["ecas"].insert_one(eca_dict).inserted_id
    return {"id": str(_id)}


@app.websocket("/ws")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    console.log(f"Opened websocket from {websocket.client.host} {websocket.client_state}")
    connection_request = await websocket.receive_json()

    try:
        object_id = ObjectId(connection_request.get("id"))
    except InvalidId:
        console.log(f"Rejecting invalid ObjectId: {connection_request}")
        await websocket.send_json({"permitted": False, "message": "Invalid node ID"})
        return

    eca_doc = db["ecas"].find_one({
        "_id": object_id
    })

    if not eca_doc.get("authorized"):
        await websocket.send_json({"permitted": False, "message": "Not authorized"})

    # By this point, the node is authorized
    await websocket.send_json({"permitted": True, "message": "Accepted connection request"})


# DNS record management

def _add_record(zone: str, record: Any):
    return db["zones"].update_one(
        {"zone": zone}, {
            "$push": {"records": record.dict()},
            "$set": {"serial": str(int(time()))}
        }
    )


@app.post("/zones/add")
def add_zone(zone: Zone, response: Response):
    if not valid_zone(zone.zone):
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"detail": "Invalid DNS zone"}

    _zone = zone.dict()
    # TODO: _zone["users"] = [authenticated_user]
    try:
        db["zones"].insert_one(_zone)
    except DuplicateKeyError:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"detail": "Zone already exists"}

    return {"success": True}


@app.post("/records/{zone}/add")
async def add_record(zone: str, record: dict, response: Response):
    if record.get("type") == "A":
        try:
            record = ARecord(**record)
        except Exception as e:
            response.status_code = status.HTTP_400_BAD_REQUEST
            return {"detail": str(e)}
    elif record.get("type") == "AAAA":
        try:
            record = AAAARecord(**record)
        except Exception as e:
            response.status_code = status.HTTP_400_BAD_REQUEST
            return {"detail": str(e)}
    else:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"detail": "Invalid record type"}

    result = _add_record(zone, record)
    if not result.upserted_id:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"detail": "Zone doesn't exist"}
