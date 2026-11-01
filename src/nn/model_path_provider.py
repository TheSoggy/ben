import os
import re
import shutil
import tempfile
import atexit
from typing import List, Optional
import logging # Optional: for better logging

import psutil

# Configure basic logging if you want to see the info messages
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Function to Determine Best Temp Path ---
def find_best_temp_drive(
    preferred_drives: List[str] = ['D', 'F', 'C'],
    temp_subdir_name: str = "app_isolated_temp"
    ) -> Optional[str]:
    """
    Checks preferred drives for existence and free space, returning a base path
    on the best available drive (most free space among existing drives).

    Args:
        preferred_drives (List[str]): A list of drive letters (e.g., ['D', 'C'])
                                      in order of preference if space is equal.
        temp_subdir_name (str): The name for the subdirectory to use/create
                                 on the chosen drive.

    Returns:
        Optional[str]: The full path to the target temporary directory base
                       (e.g., "D:\\app_isolated_temp"), or None if no suitable
                       preferred drive is found (caller should use system default).
    """
    best_drive_path = None
    max_free_space = -1
    chosen_drive_letter = None

    logging.info(f"Checking preferred drives for temporary storage: {preferred_drives}")

    drive_stats = {}

    # 1. Check existence and get free space for each preferred drive
    for drive_letter in preferred_drives:
        drive_root = f"{drive_letter.upper()}:\\"
        if os.path.exists(drive_root):
            try:
                usage = shutil.disk_usage(drive_root)
                free_space = usage.free
                drive_stats[drive_letter] = free_space
                logging.info(f"Drive {drive_letter}: Found. Free space: {free_space / (1024**3):.2f} GB")
                # Update best drive if this one has more space
                if free_space > max_free_space:
                    max_free_space = free_space
                    chosen_drive_letter = drive_letter
            except Exception as e:
                logging.warning(f"Could not get disk usage for drive {drive_letter}: {e}. Skipping.")
                drive_stats[drive_letter] = -1 # Mark as unusable
        else:
            logging.info(f"Drive {drive_letter}: Not found.")
            drive_stats[drive_letter] = -1 # Mark as non-existent/unusable

    # 2. Determine the final path
    if chosen_drive_letter is not None and max_free_space > 0:
        best_drive_path = os.path.join(f"{chosen_drive_letter}:\\", temp_subdir_name)
        logging.info(f"Selected Drive {chosen_drive_letter}: as the base for temporary files ({max_free_space / (1024**3):.2f} GB free). Path: {best_drive_path}")
    else:
        # Handle case where C was preferred but D had more space (if logic slightly changes)
        # Or if only C exists from the list
        # Or if no preferred drives were found/usable
        fallback_drive = None
        for drive_letter in preferred_drives: # Check again in preference order for *any* valid
             if drive_stats.get(drive_letter, -1) >= 0: # Check if it exists and usage was read (even if 0 free space)
                 fallback_drive = drive_letter
                 break # Take the first valid one in preference order as fallback

        if fallback_drive:
             best_drive_path = os.path.join(f"{fallback_drive}:\\", temp_subdir_name)
             logging.warning(f"Could not select based on max space, falling back to first available preferred drive: {fallback_drive}. Path: {best_drive_path}")
        else:
             logging.error("Could not find any suitable preferred drive. Will default to system temporary directory.")
             best_drive_path = None # Signal to use system default

    return best_drive_path

class IsolatedModelPathProvider:
    """
    Manages temporary copies of model files to prevent file access
    conflicts between concurrent processes.

    Provides paths to these temporary copies for external loading.
    Ensures temporary directories are cleaned up on script exit.

    Each process copies into its own directory, named
    ``isolated_<file>_<pid>_<8 random chars>``. The copy came upstream with
    "Isolated the models, when running multiple instances of BEN" (0.8.6.10):
    no two processes ever open the same model file. A directory shared
    between processes, or reading the original in place, would undo that,
    so the per-process copy stays.

    ``atexit`` is the only cleanup a process does for itself, and it never
    runs after SIGKILL. Under gunicorn a worker killed by the container's
    memory cap leaves its copies (~250 MB) behind and the arbiter forks a
    replacement that copies again; a load test that OOM-killed workers
    filled the disk in 40 minutes. So the first provider in each process
    also removes the directories of processes that are gone
    (``_sweep_orphans``). It removes only names of the exact shape above
    whose pid is not running, plus a stale directory that carries this
    process's own pid but is not one of its own copies (a reused pid).
    A directory whose pid has been reused by a live process is left alone:
    it leaks until that process exits, which bounds the leak rather than
    risking a live copy. The pid test is only meaningful when every process
    writing to the temp directory shares one pid namespace, which holds for
    a container's private ``/tmp`` and for one Windows machine.
    """
    _temp_dirs: List[str] = []  # Class attribute to track all temp dirs
    _cleanup_registered: bool = False # Ensure atexit is registered only once
    _swept_pid: Optional[int] = None # pid that last ran _sweep_orphans

    # tempfile.mkdtemp appends exactly 8 characters drawn from [a-z0-9_]
    # after the prefix "isolated_<file>_<pid>_". The suffix may itself
    # contain underscores, so the pid is the digit run just before it.
    _ORPHAN_NAME = re.compile(r"^isolated_.+_(\d+)_[a-z0-9_]{8}$")

    def __init__(self, base_temp_storage_path: Optional[str] = None):
        """
        Initializes the path provider.

        Args:
            base_temp_storage_path (Optional[str]):
                The base path on a specific disk where temporary model directories
                should be created. If None, the system's default temporary
                directory will be used. This path must exist and be writable.
        """
        self._base_temp_storage_path = base_temp_storage_path

        if self._base_temp_storage_path:
            # You might want to add checks here:
            # 1. Does the path exist?
            # 2. Is it a directory?
            # 3. Is it writable?
            if not os.path.isdir(self._base_temp_storage_path):
                try:
                    logging.info(f"Base temporary storage path '{self._base_temp_storage_path}' does not exist. Attempting to create it.")
                    os.makedirs(self._base_temp_storage_path, exist_ok=True)
                except OSError as e:
                    logging.error(f"Failed to create base temporary storage path '{self._base_temp_storage_path}': {e}. Falling back to system default.")
                    self._base_temp_storage_path = None # Fallback
            elif not os.access(self._base_temp_storage_path, os.W_OK):
                logging.warning(f"Base temporary storage path '{self._base_temp_storage_path}' is not writable. Falling back to system default.")
                self._base_temp_storage_path = None # Fallback


        # Register cleanup only once
        if not IsolatedModelPathProvider._cleanup_registered:
            atexit.register(self._cleanup_all)
            IsolatedModelPathProvider._cleanup_registered = True

        # Once per process: a forked child inherits _swept_pid from its
        # parent, so compare against the pid rather than a boolean.
        if IsolatedModelPathProvider._swept_pid != os.getpid():
            IsolatedModelPathProvider._swept_pid = os.getpid()
            self._sweep_orphans(self._base_temp_storage_path or tempfile.gettempdir())

    @classmethod
    def _sweep_orphans(cls, base_dir: str) -> int:
        """Remove model copies left by processes that no longer run.

        Returns the number of directories removed. Never raises: a failed
        sweep must not stop a worker from booting, so every failure is
        logged and the sweep moves on.
        """
        removed = 0
        own_pid = os.getpid()
        own_dirs = {os.path.abspath(d) for d in cls._temp_dirs}
        try:
            entries = os.listdir(base_dir)
        except OSError as e:
            logging.warning(f"Could not list '{base_dir}' to remove orphaned model copies: {e}")
            return 0

        for name in entries:
            match = cls._ORPHAN_NAME.match(name)
            if not match:
                continue
            path = os.path.abspath(os.path.join(base_dir, name))
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            pid = int(match.group(1))
            if pid == own_pid:
                if path in own_dirs:
                    continue
            elif psutil.pid_exists(pid):
                continue
            try:
                shutil.rmtree(path)
                removed += 1
                logging.warning(f"Removed orphaned model copy of pid {pid}: {path}")
            except OSError as e:
                logging.warning(f"Failed to remove orphaned model copy '{path}': {e}")

        return removed

    def get_path(self, original_path: str) -> str:
        """
        Copies the model file to a unique temporary location and returns its path.

        Args:
            original_path: The path to the original model file (.keras, etc.).

        Returns:
            The path to the isolated, temporary copy of the model file.

        Raises:
            FileNotFoundError: If the original_path does not exist.
            Exception: If copying fails.
        """
        if not os.path.exists(original_path):
            raise FileNotFoundError(f"Original model file not found: {original_path}")

        instance_temp_dir: Optional[str] = None
        try:
            # Create a unique temporary directory for this model copy
            # Using original filename in prefix for slightly better identification
            safe_basename = "".join(c if c.isalnum() else "_" for c in os.path.basename(original_path))
            
            # Use the specified base_temp_storage_path if provided, else system default
            instance_temp_dir = tempfile.mkdtemp(
                prefix=f"isolated_{safe_basename}_{os.getpid()}_",
                dir=self._base_temp_storage_path  # <<< THIS IS THE KEY CHANGE
            )
            self._temp_dirs.append(instance_temp_dir) # Track for cleanup

            # Construct the path for the copied file
            copied_model_path = os.path.join(instance_temp_dir, os.path.basename(original_path))

            logging.info(f"Copying '{os.path.basename(original_path)}' to temporary location: {instance_temp_dir}")
            shutil.copy2(original_path, copied_model_path) # copy2 preserves metadata
            logging.info(f"Successfully created temporary copy: {copied_model_path}")

            # Return the path to the copy
            return copied_model_path

        except Exception as e:
            # If temp dir was created but copy failed, try to clean it up immediately
            if instance_temp_dir and os.path.exists(instance_temp_dir):
                try:
                    shutil.rmtree(instance_temp_dir)
                    if instance_temp_dir in self._temp_dirs: # Should be, but good to check
                        self._temp_dirs.remove(instance_temp_dir)
                except Exception as cleanup_e:
                    logging.error(f"Failed to cleanup temporary directory '{instance_temp_dir}' after an error: {cleanup_e}")
            raise # Re-raise the original exception

    @classmethod
    def _cleanup_all(cls):
        """Static method (called by atexit) to clean up all temporary directories."""
        if not cls._temp_dirs:
            logging.info("No temporary directories to clean up.")
            return

        logging.info(f"Cleaning up {len(cls._temp_dirs)} temporary directories...")
        cleaned_count = 0
        failed_count = 0
        # Iterate over a copy because we might modify the original list (though clear() handles it)
        dirs_to_remove = list(cls._temp_dirs)
        cls._temp_dirs.clear() # Clear original list early to prevent re-processing in rare cases

        for temp_dir in dirs_to_remove:
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    logging.info(f"Successfully removed temporary directory: {temp_dir}")
                    cleaned_count += 1
                except Exception as e:
                    logging.error(f"Failed to remove temporary directory '{temp_dir}': {e}")
                    failed_count += 1
            else:
                logging.warning(f"Temporary directory already removed or not found: {temp_dir}")

        logging.info(f"Cleanup summary: {cleaned_count} directories cleaned, {failed_count} failed.")

# --- Example Usage ---
if __name__ == "__main__":
    # Configure logging for the example
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Create a dummy model file for testing
    original_model_filename = "my_model_v1.keras"
    with open(original_model_filename, "w") as f:
        f.write("This is a dummy model file.")

    # --- Scenario 1: Use system default temp directory (like your original code) ---
    print("\n--- SCENARIO 1: Using system default temp directory ---")
    provider_default = IsolatedModelPathProvider()
    try:
        temp_model_path_default = provider_default.get_path(original_model_filename)
        print(f"Original model: {original_model_filename}")
        print(f"Temporary copy (default temp): {temp_model_path_default}")
        assert os.path.exists(temp_model_path_default)
        # In a real app, you'd load the model from temp_model_path_default here
    except Exception as e:
        print(f"Error in Scenario 1: {e}")

    # --- Scenario 2: Specify a custom base path for temporary files ---
    # IMPORTANT: Change "D:/my_app_temp_storage" to a path on a disk with space
    # and where your application has write permissions.
    custom_temp_base = "D:/my_app_temp_storage" # e.g., on your D: drive
    # Create this directory if it doesn't exist for the example to run smoothly
    # The class itself will also try to create it if it doesn't exist.
    if not os.path.exists(custom_temp_base):
        os.makedirs(custom_temp_base, exist_ok=True)

    print(f"\n--- SCENARIO 2: Using custom temp directory base: {custom_temp_base} ---")
    provider_custom = IsolatedModelPathProvider(base_temp_storage_path=custom_temp_base)
    try:
        temp_model_path_custom = provider_custom.get_path(original_model_filename)
        print(f"Original model: {original_model_filename}")
        print(f"Temporary copy (custom temp base): {temp_model_path_custom}")
        assert os.path.exists(temp_model_path_custom)
        # Check if it's actually in the custom base path
        assert temp_model_path_custom.startswith(os.path.abspath(custom_temp_base))
        print(f"Verified: Temporary copy is inside '{os.path.abspath(custom_temp_base)}'")
    except Exception as e:
        print(f"Error in Scenario 2: {e}")


    # --- Scenario 3: Specify a non-existent custom base path that CAN be created ---
    creatable_temp_base = "D:/creatable_app_temp"
    if os.path.exists(creatable_temp_base): # cleanup from previous run
        shutil.rmtree(creatable_temp_base)

    print(f"\n--- SCENARIO 3: Using creatable custom temp directory base: {creatable_temp_base} ---")
    provider_creatable = IsolatedModelPathProvider(base_temp_storage_path=creatable_temp_base)
    try:
        temp_model_path_creatable = provider_creatable.get_path(original_model_filename)
        print(f"Original model: {original_model_filename}")
        print(f"Temporary copy (creatable temp base): {temp_model_path_creatable}")
        assert os.path.exists(temp_model_path_creatable)
        assert temp_model_path_creatable.startswith(os.path.abspath(creatable_temp_base))
        print(f"Verified: Temporary copy is inside '{os.path.abspath(creatable_temp_base)}'")
    except Exception as e:
        print(f"Error in Scenario 3: {e}")


    # --- Scenario 4: Specify a problematic custom base path (e.g., a file, or no permission) ---
    # This part is harder to make universally testable without knowing user's FS permissions
    # Let's simulate a path that can't be created (e.g. by making a file with that name)
    problematic_temp_base = "D:/problematic_temp_dir_base"
    # Create a file at this path to make it an invalid directory for os.makedirs
    try:
        with open(problematic_temp_base, "w") as f:
            f.write("this is a file, not a dir")

        print(f"\n--- SCENARIO 4: Using problematic custom temp directory base: {problematic_temp_base} ---")
        # The __init__ should log a warning and fallback to system default
        provider_problematic = IsolatedModelPathProvider(base_temp_storage_path=problematic_temp_base)
        assert provider_problematic._base_temp_storage_path is None # Check fallback
        print("Provider fell back to system default as expected.")

        temp_model_path_problematic = provider_problematic.get_path(original_model_filename)
        print(f"Original model: {original_model_filename}")
        print(f"Temporary copy (problematic, so fallback to system temp): {temp_model_path_problematic}")
        assert os.path.exists(temp_model_path_problematic)
        # It should NOT be in the problematic path
        assert not temp_model_path_problematic.startswith(os.path.abspath(problematic_temp_base))
    except Exception as e:
        print(f"Error in Scenario 4: {e}")
    finally:
        if os.path.exists(problematic_temp_base):
            os.remove(problematic_temp_base) # Clean up the dummy file

    print("\nDummy model file and temporary directories will be cleaned up on script exit (by atexit).")
    # To manually clean up the original dummy model:
    # os.remove(original_model_filename)