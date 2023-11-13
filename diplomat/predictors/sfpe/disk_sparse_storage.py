from collections import deque
from typing import BinaryIO, Any, Dict, Optional
from diplomat.predictors.fpe.sparse_storage import ForwardBackwardData, AttributeDict, ForwardBackwardFrame, SettableSequence
from diplomat.predictors.sfpe.file_io import DiplomatFPEState
import multiprocessing


class MonitoredAttributeDict(AttributeDict):
    def __init__(self, backing: DiplomatFPEState):
        self._backing = backing
        super().__init__(backing.get_metadata())

    def __setitem__(self, key: str, val: Any):
        super().__setitem__(key, val)
        self._backing.set_metadata(dict(self.data))


class LIFOCache:
    def __init__(self, size: int):
        self._data = {}
        self._queue = deque()
        self._size = size

    def get(self, index: int, backing: SettableSequence) -> Any:
        if(index in self._data):
            return self._data[index]

        self._data[index] = backing[index]
        self._queue.append(index)

        self._clean_to(self._size, backing)
        return self._data[index]

    def set(self, index: int, backing: SettableSequence, value: Any):
        if(index not in self._data[index]):
            self._queue.append(index)
        self._data[index] = value

        self._clean_to(self._size, backing)

    def _clean_to(self, amount: int, backing: SettableSequence):
        while(len(self._queue) > amount):
            idx = self._queue.popleft()
            backing[idx] = self._data[idx]

    def flush(self, backing: SettableSequence):
        self._clean_to(0, backing)


class CacheList:
    def __init__(
        self,
        backing: SettableSequence,
        cache: LIFOCache,
        start: int = 0,
        stop: int = None,
        step: int = None
    ):
        self._backing = backing
        self._cache = cache
        stop = stop if(stop is not None) else len(backing)
        self._range = range(start, stop, step)

    def __getitem__(self, index):
        if(isinstance(index, slice)):
            r = self._range[index]
            return CacheList(self._backing, self._cache, r.start, r.stop, r.step)
        return self._cache.get(self._range[index], self._backing)

    def __setitem__(self, key, value):
        if(isinstance(key, slice)):
            for i, index in enumerate(self._range[key]):
                self._cache.set(index, self._backing, value[i])
        self._cache.set(key, self._backing, value)

    def __len__(self):
        return len(self._range)


class CacheListContainer:
    def __init__(
        self,
        backing: SettableSequence,
        cache: LIFOCache,
        jump: int
    ):
        self._backing = backing
        self._cache = cache
        self._jump = jump

    def __getitem__(self, item: int):
        if(item < 0 or item > len(self)):
            raise IndexError("Index out of bounds.")
        return CacheList(self._backing, self._cache, self._jump * item, self._jump * (item + 1), 1)

    def __setitem__(self, key: int, value: CacheList):
        if(key < 0 or key > len(self)):
            raise IndexError("Index out of bounds.")
        CacheList(self._backing, self._cache, self._jump * key, self._jump * (key + 1), 1)[:] = value

    def __len__(self):
        return len(self._backing) // self._jump


class DummyLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class DiskBackedForwardBackwardData(ForwardBackwardData):
    """
    A version of ForwardBackwardData that stores its results on disk instead of
    """

    def __init__(
        self,
        num_frames: int,
        num_bp: int,
        file_obj: BinaryIO,
        cache_size: int = 100,
        lock: Optional[multiprocessing.Lock] = None,
        **kwargs
    ):
        """
        Create a new ForwardBackwardData list/object.

        :param num_frames: Number of frames to allocate space for.
        :param num_bp: Number of body parts to allocate space for.
        :param file_obj: The file to use to store files on disk.
        :param kwargs: Additional arguments passed to the storage backend.
        """
        super().__init__(0, 0)
        self._num_bps = num_bp
        self._num_frames = num_frames
        self._lock = DummyLock() if(lock is None) else lock
        self._cache = LIFOCache(cache_size)
        self.allow_pickle = True

        self._file_obj = file_obj

        self._frames = DiplomatFPEState(self._file_obj, frame_count=num_frames * num_bp, **kwargs)
        self._metadata = MonitoredAttributeDict(self._frames)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._frames.close()

    def _flush_cache(self):
        # Flush the cache, deleting any new entries...
        self._cache.flush(self._frames)

    def _flush_meta(self):
        self._frames.set_metadata(dict(self._metadata))

    def close(self):
        self._flush_cache()
        self._flush_meta()
        self._frames.close()

    @property
    def frames(self) -> SettableSequence[SettableSequence[ForwardBackwardFrame]]:
        """
        Get/Set the frames of this ForwardBackwardData, a 2D list of ForwardBackwardFrame. Indexing is frame, then body
        part.
        """
        return CacheListContainer(
            self._frames,
            self._cache,
            self._num_bps
        )

    @frames.setter
    def frames(self, frames: SettableSequence[SettableSequence[ForwardBackwardFrame]]):
        """
        Frames setter...
        """
        raise NotImplementedError("Not supported for disk backed frame storage.")

    @property
    def num_bodyparts(self) -> int:
        """
        Read-Only: Get the number of body parts stored in this forward backward data object...
        """
        return self._num_bps

    @property
    def num_frames(self) -> int:
        """
        Read-Only: Get the number of frames stored in this forward backward data object...
        """
        return self._num_frames

    @property
    def metadata(self) -> AttributeDict:
        """
        Get/Set the metadata of this forward backward data object. This property can be set to any mapping/dictionary
        type of string to values, but always returns an AttributeDict to allow dot operator access to properties.
        """
        return self._metadata

    @metadata.setter
    def metadata(self, meta: Dict[str, Any]):
        """
        Metadata setter...
        """
        if(self._metadata is meta):
            self._metadata = meta
            return
        self._frames.set_metadata(dict(meta))
        self._metadata = MonitoredAttributeDict(self._frames)

    def copy(self) -> "DiskBackedForwardBackwardData":
        """
        Copy this ForwardBackwardData, returning a new one.
        """
        self._flush_cache()
        self._flush_meta()
        res = type(self)(self._num_frames, self._num_bps, self._file_obj)
        res.allow_pickle = self.allow_pickle
        return res
