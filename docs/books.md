# gwt-books

A parish register on an image viewer has no name index, but its images run in date order. To reach one date you open an image, read its date, and decide where to go next. `gwt-books` keeps those readings and says which image to open next, like a binary search that uses every date already seen.

It is offline. It never fetches an image, never talks to a site and never needs a login. A person opens the image and types what it showed. That keeps it inside the terms of image viewers that forbid bulk or automated download.

Standard library only. Run it as `books/gwt-books` or `python3 books/gwt_books.py`.

## The file

A TSV with a header: `book`, `image`, `date`, `note`, `seen`. `book` is any label you choose for one register (a catalogue reference, a roll, a folder). The path comes from `--log`, else `$GWT_BOOKS`, else `./libros.tsv`. Reading an image twice keeps the latest reading.

Dates are `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; a short date means its first day.

## Commands

```sh
# you opened image 120 of the register and it shows March 1905
gwt-books add  --book register-1 --image 120 --date 1905-03 --note "baptisms"

# which image next, for a baptism of 1926? --total is the number of images in the book
gwt-books next --book register-1 --target 1926-05 --total 640

# the landmarks so far, and the window the target is still in
gwt-books show --book register-1 --target 1926-05
```

`next` picks the image by linear interpolation between the nearest landmarks before and after the target. It never picks an image you already read. With no landmarks it opens the middle of the book (it needs `--total`). With landmarks on one side only it moves toward the other end. It prints how many images are left in the window. When two images next to each other bracket the target, it says so and stops: the page is not in this register, or the register skips that date. It exits 1 when it has no image to suggest.

## What it does not do

It does not read the image for you, and it does not know whether the register is sorted by date. Baptisms are usually sorted; a register that mixes years will mislead the interpolation, and the landmarks will show that when the dates stop rising.
