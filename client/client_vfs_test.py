#!/usr/bin/env python
# -*- mode: python; encoding: utf-8 -*-

"""Test client vfs."""


import os
import platform
import stat


import psutil

# pylint: disable=unused-import,g-bad-import-order
from grr.client import client_plugins
# pylint: enable=unused-import,g-bad-import-order

from grr.client import conf
import logging
from grr.client import vfs
from grr.client.vfs_handlers import files
from grr.lib import rdfvalue
from grr.lib import test_lib
from grr.lib import utils


def setUp():
  # Initialize the VFS system
  vfs.VFSInit()


class VFSTest(test_lib.GRRBaseTest):
  """Test the client VFS switch."""

  def GetNumbers(self):
    """Generate a test string."""
    result = ""
    for i in range(1, 1001):
      result += "%s\n" % i

    return result

  def TestFileHandling(self, fd):
    """Test the file like object behaviour."""
    original_string = self.GetNumbers()

    self.assertEqual(fd.size, len(original_string))

    fd.Seek(0)
    self.assertEqual(fd.Read(100), original_string[0:100])
    self.assertEqual(fd.Tell(), 100)

    fd.Seek(-10, 1)
    self.assertEqual(fd.Tell(), 90)
    self.assertEqual(fd.Read(10), original_string[90:100])

    fd.Seek(0, 2)
    self.assertEqual(fd.Tell(), len(original_string))
    self.assertEqual(fd.Read(10), "")
    self.assertEqual(fd.Tell(), len(original_string))

    # Raise if we try to list the contents of a file object.
    self.assertRaises(IOError, lambda: list(fd.ListFiles()))

  def testRegularFile(self):
    """Test our ability to read regular files."""
    path = os.path.join(self.base_path, "morenumbers.txt")
    pathspec = rdfvalue.RDFPathSpec(path=path,
                                    pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    fd = vfs.VFSOpen(pathspec)

    self.TestFileHandling(fd)

  def testOpenFilehandles(self):
    """Test that file handles are cached."""
    current_process = psutil.Process(os.getpid())
    num_open_files = len(current_process.get_open_files())

    path = os.path.join(self.base_path, "morenumbers.txt")

    fds = []
    for _ in range(100):
      fd = vfs.VFSOpen(
          rdfvalue.RDFPathSpec(path=path,
                               pathtype=rdfvalue.RDFPathSpec.Enum("OS")))
      self.assertEqual(fd.read(20), "1\n2\n3\n4\n5\n6\n7\n8\n9\n10")
      fds.append(fd)

    # This should not create any new file handles.
    self.assertTrue(len(current_process.get_open_files()) - num_open_files < 5)

  def testOpenFilehandlesExpire(self):
    """Test that file handles expire from cache."""
    files.FILE_HANDLE_CACHE = utils.FastStore(max_size=10)

    current_process = psutil.Process(os.getpid())
    num_open_files = len(current_process.get_open_files())

    path = os.path.join(self.base_path, "morenumbers.txt")
    fd = vfs.VFSOpen(
        rdfvalue.RDFPathSpec(path=path,
                             pathtype=rdfvalue.RDFPathSpec.Enum("OS")))

    fds = []
    for filename in fd.ListNames():
      child_fd = vfs.VFSOpen(
          rdfvalue.RDFPathSpec(path=os.path.join(path, filename),
                               pathtype=rdfvalue.RDFPathSpec.Enum("OS")))
      fd.read(20)
      fds.append(child_fd)

    # This should not create any new file handles.
    self.assertTrue(len(current_process.get_open_files()) - num_open_files < 5)

    # Make sure we exceeded the size of the cache.
    self.assert_(fds > 20)

  def testFileCasing(self):
    """Test our ability to read the correct casing from filesystem."""
    path = os.path.join(self.base_path, "numbers.txt")
    try:
      os.lstat(os.path.join(self.base_path, "nUmBeRs.txt"))
      os.lstat(os.path.join(self.base_path, "nuMbErs.txt"))
      # If we reached this point we are on a case insensitive file system
      # and the tests below do not make any sense.
      logging.warning("Case insensitive file system detected. Skipping test.")
      return
    except (IOError, OSError):
      pass

    fd = vfs.VFSOpen(
        rdfvalue.RDFPathSpec(path=path,
                             pathtype=rdfvalue.RDFPathSpec.Enum("OS")))
    self.assertEqual(fd.pathspec.Basename(), "numbers.txt")

    path = os.path.join(self.base_path, "numbers.TXT")

    fd = vfs.VFSOpen(
        rdfvalue.RDFPathSpec(path=path,
                             pathtype=rdfvalue.RDFPathSpec.Enum("OS")))
    self.assertEqual(fd.pathspec.Basename(), "numbers.TXT")

    path = os.path.join(self.base_path, "Numbers.txt")
    fd = vfs.VFSOpen(
        rdfvalue.RDFPathSpec(path=path,
                             pathtype=rdfvalue.RDFPathSpec.Enum("OS")))
    read_path = fd.pathspec.Basename()

    # The exact file now is non deterministic but should be either of the two:
    if read_path != "numbers.txt" and read_path != "numbers.TXT":
      raise RuntimeError("read path is %s" % read_path)

    # Ensure that the produced pathspec specified no case folding:
    s = fd.Stat()
    self.assertEqual(s.pathspec.path_options,
                     rdfvalue.RDFPathSpec.Enum("CASE_LITERAL"))

    # Case folding will only occur when requested - this should raise because we
    # have the CASE_LITERAL option:
    pathspec = rdfvalue.RDFPathSpec(
        path=path,
        pathtype=rdfvalue.RDFPathSpec.Enum("OS"),
        path_options=rdfvalue.RDFPathSpec.Enum("CASE_LITERAL"))
    self.assertRaises(IOError, vfs.VFSOpen, pathspec)

  def testTSKFile(self):
    """Test our ability to read from image files."""
    path = os.path.join(self.base_path, "test_img.dd")
    path2 = "Test Directory/numbers.txt"

    p2 = rdfvalue.RDFPathSpec(path=path2,
                              pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))
    p1 = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    p1.Append(p2)
    fd = vfs.VFSOpen(p1)
    self.TestFileHandling(fd)

  def testTSKFileInode(self):
    """Test opening a file through an indirect pathspec."""
    pathspec = rdfvalue.RDFPathSpec(
        path=os.path.join(self.base_path, "test_img.dd"),
        pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    pathspec.Append(pathtype=rdfvalue.RDFPathSpec.Enum("TSK"), inode=12,
                    path="/Test Directory")
    pathspec.Append(pathtype=rdfvalue.RDFPathSpec.Enum("TSK"),
                    path="numbers.txt")

    fd = vfs.VFSOpen(pathspec)

    # Check that the new pathspec is correctly reduced to two components.
    self.assertEqual(
        fd.pathspec.first.path,
        os.path.normpath(os.path.join(self.base_path, "test_img.dd")))
    self.assertEqual(fd.pathspec[1].path, "/Test Directory/numbers.txt")

    # And the correct inode is placed in the final branch.
    self.assertEqual(fd.Stat().pathspec.nested_path.inode, 15)
    self.TestFileHandling(fd)

  def testTSKFileCasing(self):
    """Test our ability to read the correct casing from image."""
    path = os.path.join(self.base_path, "test_img.dd")
    path2 = os.path.join("test directory", "NuMbErS.TxT")

    ps2 = rdfvalue.RDFPathSpec(
        path=path2,
        pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))

    ps = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    ps.Append(ps2)
    fd = vfs.VFSOpen(ps)

    # This fixes Windows paths.
    path = path.replace("\\", "/")
    # The pathspec should have 2 components.

    self.assertEqual(fd.pathspec.first.path,
                     utils.NormalizePath(path))
    self.assertEqual(fd.pathspec.first.pathtype,
                     rdfvalue.RDFPathSpec.Enum("OS"))

    nested = fd.pathspec.last
    self.assertEqual(nested.path, u"/Test Directory/numbers.txt")
    self.assertEqual(nested.pathtype, rdfvalue.RDFPathSpec.Enum("TSK"))

  def testTSKInodeHandling(self):
    """Test that we can open files by inode."""
    path = os.path.join(self.base_path, "ntfs_img.dd")
    ps2 = rdfvalue.RDFPathSpec(
        inode=65, ntfs_type=128, ntfs_id=0,
        path="/this/will/be/ignored",
        pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))

    ps = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"),
                              offset=63*512)
    ps.Append(ps2)
    fd = vfs.VFSOpen(ps)

    self.assertEqual(fd.Read(100), "Hello world\n")

    ps2 = rdfvalue.RDFPathSpec(inode=65, ntfs_type=128, ntfs_id=4,
                               pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))
    ps = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"),
                              offset=63*512)
    ps.Append(ps2)
    fd = vfs.VFSOpen(ps)

    self.assertEqual(fd.read(100), "I am a real ADS\n")

    # Make sure the size is correct:
    self.assertEqual(fd.Stat().st_size, len("I am a real ADS\n"))

  def testTSKNTFSHandling(self):
    """Test that TSK can correctly encode NTFS features."""
    path = os.path.join(self.base_path, "ntfs_img.dd")
    path2 = "test directory"

    ps2 = rdfvalue.RDFPathSpec(path=path2,
                               pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))

    ps = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"),
                              offset=63*512)
    ps.Append(ps2)
    fd = vfs.VFSOpen(ps)

    # This fixes Windows paths.
    path = path.replace("\\", "/")
    listing = []
    pathspecs = []

    for f in fd.ListFiles():
      # Make sure the CASE_LITERAL option is set for all drivers so we can just
      # resend this proto back.
      self.assertEqual(f.pathspec.path_options,
                       rdfvalue.RDFPathSpec.Enum("CASE_LITERAL"))
      pathspec = f.pathspec.nested_path
      self.assertEqual(pathspec.path_options,
                       rdfvalue.RDFPathSpec.Enum("CASE_LITERAL"))
      pathspecs.append(f.pathspec)
      listing.append((pathspec.inode, pathspec.ntfs_type, pathspec.ntfs_id))

    ref = [(65, rdfvalue.RDFPathSpec.Enum("TSK_FS_ATTR_TYPE_DEFAULT"), 0),
           (65, rdfvalue.RDFPathSpec.Enum("TSK_FS_ATTR_TYPE_NTFS_DATA"), 4),
           (66, rdfvalue.RDFPathSpec.Enum("TSK_FS_ATTR_TYPE_DEFAULT"), 0),
           (67, rdfvalue.RDFPathSpec.Enum("TSK_FS_ATTR_TYPE_DEFAULT"), 0)]

    # Make sure that the ADS is recovered.
    self.assertEqual(listing, ref)

    # Try to read the main file
    self.assertEqual(pathspecs[0].nested_path.path, "/Test Directory/notes.txt")
    fd = vfs.VFSOpen(pathspecs[0])
    self.assertEqual(fd.read(1000), "Hello world\n")

    s = fd.Stat()
    self.assertEqual(s.pathspec.nested_path.inode, 65)
    self.assertEqual(s.pathspec.nested_path.ntfs_type, 1)
    self.assertEqual(s.pathspec.nested_path.ntfs_id, 0)

    # Check that the name of the ads is consistent.
    self.assertEqual(pathspecs[1].nested_path.path,
                     "/Test Directory/notes.txt:ads")
    fd = vfs.VFSOpen(pathspecs[1])
    self.assertEqual(fd.read(1000), "I am a real ADS\n")

    # Test that the stat contains the inode:
    s = fd.Stat()
    self.assertEqual(s.pathspec.nested_path.inode, 65)
    self.assertEqual(s.pathspec.nested_path.ntfs_type, 128)
    self.assertEqual(s.pathspec.nested_path.ntfs_id, 4)

  def testUnicodeFile(self):
    """Test ability to read unicode files from images."""
    path = os.path.join(self.base_path, "test_img.dd")
    path2 = os.path.join(u"איןד ןד ש אקדא", u"איןד.txt")

    ps2 = rdfvalue.RDFPathSpec(path=path2,
                               pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))

    ps = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    ps.Append(ps2)
    fd = vfs.VFSOpen(ps)
    self.TestFileHandling(fd)

  def testListDirectory(self):
    """Test our ability to list a directory."""
    directory = vfs.VFSOpen(
        rdfvalue.RDFPathSpec(path=self.base_path,
                             pathtype=rdfvalue.RDFPathSpec.Enum("OS")))

    self.CheckDirectoryListing(directory, "morenumbers.txt")

  def testTSKListDirectory(self):
    """Test directory listing in sleuthkit."""
    path = os.path.join(self.base_path, u"test_img.dd")
    ps2 = rdfvalue.RDFPathSpec(path=u"入乡随俗 海外春节别样过法",
                               pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))
    ps = rdfvalue.RDFPathSpec(path=path,
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    ps.Append(ps2)
    directory = vfs.VFSOpen(ps)
    self.CheckDirectoryListing(directory, u"入乡随俗.txt")

  def testRecursiveImages(self):
    """Test directory listing in sleuthkit."""
    p3 = rdfvalue.RDFPathSpec(path="/home/a.txt",
                              pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))
    p2 = rdfvalue.RDFPathSpec(path="/home/image2.img",
                              pathtype=rdfvalue.RDFPathSpec.Enum("TSK"))
    p1 = rdfvalue.RDFPathSpec(path=os.path.join(self.base_path, "test_img.dd"),
                              pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    p2.Append(p3)
    p1.Append(p2)
    f = vfs.VFSOpen(p1)

    self.assertEqual(f.read(3), "yay")

  def testGuessPathSpec(self):
    """Test that we can guess a pathspec from a path."""
    path = os.path.join(self.base_path, "test_img.dd", "home/image2.img",
                        "home/a.txt")

    pathspec = rdfvalue.RDFPathSpec(path=path,
                                    pathtype=rdfvalue.RDFPathSpec.Enum("OS"))

    fd = vfs.VFSOpen(pathspec)
    self.assertEqual(fd.read(3), "yay")

  def testFileNotFound(self):
    """Test that we raise an IOError for file not found."""
    path = os.path.join(self.base_path, "test_img.dd", "home/image2.img",
                        "home/nosuchfile.txt")

    pathspec = rdfvalue.RDFPathSpec(path=path,
                                    pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    self.assertRaises(IOError, vfs.VFSOpen, pathspec)

  def testGuessPathSpecPartial(self):
    """Test that we can guess a pathspec from a partial pathspec."""
    path = os.path.join(self.base_path, "test_img.dd")
    pathspec = rdfvalue.RDFPathSpec(path=path,
                                    pathtype=rdfvalue.RDFPathSpec.Enum("OS"))
    pathspec.nested_path.path = "/home/image2.img/home/a.txt"
    pathspec.nested_path.pathtype = rdfvalue.RDFPathSpec.Enum("TSK")

    fd = vfs.VFSOpen(pathspec)
    self.assertEqual(fd.read(3), "yay")

    # Open as a directory
    pathspec.nested_path.path = "/home/image2.img/home/"
    fd = vfs.VFSOpen(pathspec)

    names = []
    for s in fd.ListFiles():
      # Make sure that the stat pathspec is correct - it should be 3 levels
      # deep.
      self.assertEqual(s.pathspec.nested_path.path, "/home/image2.img")
      names.append(s.pathspec.nested_path.nested_path.path)

    self.assertTrue("/home/a.txt" in names)

  def testRegistryListing(self):
    """Test our ability to list registry keys."""
    if platform.system() != "Windows":
      return

    # Make a value we can test for
    import _winreg  # pylint: disable=C6204

    key = _winreg.OpenKey(_winreg.HKEY_CURRENT_USER,
                          "Software",
                          0,
                          _winreg.KEY_CREATE_SUB_KEY)
    subkey = _winreg.CreateKey(key, "GRR_Test")
    _winreg.SetValueEx(subkey, "foo", 0, _winreg.REG_SZ, "bar")

    vfs_path = "HKEY_CURRENT_USER/Software/GRR_Test"

    pathspec = rdfvalue.RDFPathSpec(
        path=vfs_path,
        pathtype=rdfvalue.RDFPathSpec.Enum("REGISTRY"))
    for f in vfs.VFSOpen(pathspec).ListFiles():
      self.assertEqual(f.pathspec.path, "/" + vfs_path + "/foo")
      self.assertEqual(f.resident, "bar")

    _winreg.DeleteKey(key, "GRR_Test")

  def CheckDirectoryListing(self, directory, test_file):
    """Check that the directory listing is sensible."""

    found = False
    for f in directory.ListFiles():
      # TSK makes virtual files with $ if front of them
      path = f.pathspec.Basename()
      if path.startswith("$"): continue

      # Check the time is reasonable
      self.assert_(f.st_mtime > 10000000)
      self.assert_(f.st_atime > 10000000)
      self.assert_(f.st_ctime > 10000000)

      if path == test_file:
        found = True
        # Make sure its a regular file with the right size
        self.assert_(stat.S_ISREG(f.st_mode))
        self.assertEqual(f.st_size, 3893)

    self.assertEqual(found, True)

    # Raise if we try to read the contents of a directory object.
    self.assertRaises(IOError, directory.Read, 5)


def main(argv):
  vfs.VFSInit()
  test_lib.main(argv)

if __name__ == "__main__":
  conf.StartMain(main)