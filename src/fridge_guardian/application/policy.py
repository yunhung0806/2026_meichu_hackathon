from fridge_guardian.domain import DecisionCode, Item


def ownership_decision(item: Item, acting_user_id: str, shared: bool) -> DecisionCode:
    if item.owner_id == acting_user_id:
        return DecisionCode.ALLOW_OWNER
    if shared:
        return DecisionCode.ALLOW_SHARED
    return DecisionCode.WARN_NOT_OWNER
