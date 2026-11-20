use std::sync::atomic::{AtomicUsize, Ordering};

/// Remaining tokens in the current window. The concurrent decrement is the
/// suspected race; tests currently pass because they are single-threaded.
pub static REMAINING: AtomicUsize = AtomicUsize::new(100);

pub fn allow_login() -> Result<(), ()> {
    let left = REMAINING.load(Ordering::SeqCst);
    if left == 0 {
        return Err(());
    }
    REMAINING.store(left - 1, Ordering::SeqCst);
    Ok(())
}

pub fn reset() {
    REMAINING.store(100, Ordering::SeqCst);
}
