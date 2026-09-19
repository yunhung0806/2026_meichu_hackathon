from fridge_guardian.domain import Action


class ManualActionSource:
    """Keyboard implementation of the replaceable ActionSource contract."""

    _KEYS = {
        ord("p"): Action.PUT_IN,
        ord("P"): Action.PUT_IN,
        ord("t"): Action.TAKE_OUT,
        ord("T"): Action.TAKE_OUT,
    }

    def action_for_key(self, key_code: int) -> Action | None:
        return self._KEYS.get(key_code)
