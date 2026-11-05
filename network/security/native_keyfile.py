"""Offline libnm profile validation through its C API; never connects to NM."""
import ctypes
import ctypes.util


class GError(ctypes.Structure):
    _fields_=[('domain',ctypes.c_uint),('code',ctypes.c_int),('message',ctypes.c_char_p)]


def verify(text):
    for library in ('glib-2.0','nm','gobject-2.0'):
        if not ctypes.util.find_library(library):
            raise RuntimeError('Fehlende Profilpruefbibliothek: '+library)
    glib=ctypes.CDLL(ctypes.util.find_library('glib-2.0'))
    nm=ctypes.CDLL(ctypes.util.find_library('nm'))
    gobject=ctypes.CDLL(ctypes.util.find_library('gobject-2.0'))
    error_type=ctypes.POINTER(ctypes.POINTER(GError))
    glib.g_key_file_new.restype=ctypes.c_void_p
    glib.g_key_file_load_from_data.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_size_t,ctypes.c_int,error_type]
    glib.g_key_file_unref.argtypes=[ctypes.c_void_p]
    glib.g_error_free.argtypes=[ctypes.POINTER(GError)]
    nm.nm_keyfile_read.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,error_type]
    nm.nm_keyfile_read.restype=ctypes.c_void_p
    nm.nm_connection_verify.argtypes=[ctypes.c_void_p,error_type]
    gobject.g_object_unref.argtypes=[ctypes.c_void_p]
    error=ctypes.POINTER(GError)();key=glib.g_key_file_new();connection=None
    try:
        data=text.encode()
        if not glib.g_key_file_load_from_data(key,data,len(data),0,ctypes.byref(error)):
            raise ValueError(error.contents.message.decode() if error else 'Invalid keyfile')
        connection=nm.nm_keyfile_read(key,b'/',0,None,None,ctypes.byref(error))
        if not connection:
            raise ValueError(error.contents.message.decode() if error else 'Profile parse failed')
        if not nm.nm_connection_verify(connection,ctypes.byref(error)):
            raise ValueError(error.contents.message.decode() if error else 'Profile verify failed')
    finally:
        if connection:gobject.g_object_unref(connection)
        if error:glib.g_error_free(error)
        glib.g_key_file_unref(key)
