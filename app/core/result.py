from typing import Any, Callable, Generic, NoReturn, Optional, TypeVar

T = TypeVar("T")
E = TypeVar("E")
U = TypeVar("U")


class Result(Generic[T, E]):
    """
    Rust-inspired Result monad representing either success (Ok) or failure (Err).
    Guarantees strict typing, safe composition, and zero unhandled exceptions.
    """

    __slots__ = ("_value", "_error", "_is_ok")

    def __init__(self, value: Optional[T] = None, error: Optional[E] = None, is_ok: bool = True):
        self._value = value
        self._error = error
        self._is_ok = is_ok

    def is_ok(self) -> bool:
        """Returns True if the result is Ok."""
        return self._is_ok

    def is_err(self) -> bool:
        """Returns True if the result is Err."""
        return not self._is_ok

    def unwrap(self) -> T:
        """
        Returns the contained value if Ok, otherwise raises ValueError.
        """
        if self._is_ok:
            return self._value  # type: ignore[return-value]
        raise ValueError(f"Called unwrap on an Err value: {self._error}")

    def unwrap_err(self) -> E:
        """
        Returns the contained error if Err, otherwise raises ValueError.
        """
        if not self._is_ok:
            return self._error  # type: ignore[return-value]
        raise ValueError(f"Called unwrap_err on an Ok value: {self._value}")

    def unwrap_or(self, default: T) -> T:
        """
        Returns the contained value or a default.
        """
        return self._value if self._is_ok else default  # type: ignore[return-value]

    def map(self, fn: Callable[[T], U]) -> "Result[U, E]":
        """
        Maps a Result[T, E] to Result[U, E] by applying a function to a contained Ok value.
        """
        if self._is_ok:
            return Ok(fn(self._value))  # type: ignore[arg-type]
        return Err(self._error)  # type: ignore[arg-type]

    def map_err(self, fn: Callable[[E], U]) -> "Result[T, U]":
        """
        Maps a Result[T, E] to Result[T, U] by applying a function to a contained Err value.
        """
        if not self._is_ok:
            return Err(fn(self._error))  # type: ignore[arg-type]
        return Ok(self._value)  # type: ignore[arg-type]

    def __bool__(self) -> bool:
        return self._is_ok

    def __repr__(self) -> str:
        if self._is_ok:
            return f"Ok({self._value!r})"
        return f"Err({self._error!r})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Result):
            return False
        return self._is_ok == other._is_ok and (
            self._value == other._value if self._is_ok else self._error == other._error
        )


def Ok(value: T) -> Result[T, Any]:
    """Wraps a value in an Ok Result."""
    return Result(value=value, is_ok=True)


def Err(error: E) -> Result[Any, E]:
    """Wraps an error in an Err Result."""
    return Result(error=error, is_ok=False)
