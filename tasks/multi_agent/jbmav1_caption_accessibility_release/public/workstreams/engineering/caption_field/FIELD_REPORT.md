
# Initial field report

Providers send cumulative partial text and may restart sequence
numbers after reconnect. Exact replay must not duplicate captions.
Already-stable visible words should not jump. The patch may not
add a long buffering window. The four supplied tests reproduce
every behavior known at task start.
