from typing import Optional
from app.schemas.item import ItemCreate, ItemResponse
from app.core.database import get_supabase_admin


class ItemService:
    def __init__(self):
        self.table = "items"

    async def get_all(self) -> list[ItemResponse]:
        db = get_supabase_admin()
        response = db.table(self.table).select("*").execute()
        return response.data or []

    async def get_by_id(self, item_id: str) -> Optional[ItemResponse]:
        db = get_supabase_admin()
        response = (
            db.table(self.table)
            .select("*")
            .eq("id", item_id)
            .single()
            .execute()
        )
        return response.data

    async def create(self, payload: ItemCreate) -> ItemResponse:
        db = get_supabase_admin()
        response = (
            db.table(self.table)
            .insert(payload.model_dump())
            .execute()
        )
        return response.data[0]

    async def delete(self, item_id: str) -> bool:
        db = get_supabase_admin()
        response = (
            db.table(self.table)
            .delete()
            .eq("id", item_id)
            .execute()
        )
        return len(response.data) > 0
