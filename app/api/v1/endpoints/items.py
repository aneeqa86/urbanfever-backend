from fastapi import APIRouter, HTTPException
from app.schemas.item import ItemCreate, ItemResponse
from app.services.item_service import ItemService

router = APIRouter()
service = ItemService()


@router.get("", response_model=list[ItemResponse])
async def get_items():
    return await service.get_all()


@router.get("/{item_id}", response_model=ItemResponse)
async def get_item(item_id: str):
    item = await service.get_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("", response_model=ItemResponse, status_code=201)
async def create_item(payload: ItemCreate):
    return await service.create(payload)


@router.delete("/{item_id}", status_code=204)
async def delete_item(item_id: str):
    deleted = await service.delete(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found")
