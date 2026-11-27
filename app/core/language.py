from typing import Literal


Language = Literal["en", "ta"]


async def preferred_language(user_id: str) -> Language:
    from bson import ObjectId

    from app.models.user import User

    user = await User.get(ObjectId(user_id)) if ObjectId.is_valid(user_id) else None
    return user.preferred_language if user and not user.is_deleted else "en"
