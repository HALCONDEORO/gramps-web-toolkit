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

# how far the search is: landmarks, dates covered, the next image and the zone left
gwt-books status --book register-1 --target 1926-05 --total 640

# the landmarks as CSV, to a file or to the screen (never over the log file itself)
gwt-books export --book register-1 --out register-1.csv
```

`next` picks the image by linear interpolation between the nearest landmarks before and after the target. It never picks an image you already read. With no landmarks it opens the middle of the book (it needs `--total`). With landmarks on one side only it moves toward the other end. It prints the zone still to review (`remaining zone: image 101 to image 199 (99 images)`). With landmarks on one side only, the zone runs to the end of the book (or to `--total`) or back to image 1. When no image is left between the landmarks it says `remaining zone: none`. When two images next to each other bracket the target, it says so and stops: the date is on one of those two images, or on a page the images do not show. Review both. A date per image is all it records, so it cannot tell which page of an image holds a given date. It exits 1 when it has no image to suggest. `add` refuses a tab or line break in `--book` or `--note`, an empty book, and an image or `--total` below 1.

## Where the images come from

On FamilySearch an unindexed collection is a "Browse Images" collection, grouped in waypoints. The viewer shows the current image number and how many images the group has (that count is your `--total`), and lets you type an image number to jump to it. See [Search unindexed record images](https://www.familysearch.org/en/help/helpcenter/article/how-do-i-find-an-image-in-an-unindexed-collection-in-historical-records). The *Explore Historical Images* tool (Search, then Images) narrows images by place and date before you browse, so use it first to get the right group: [announcement](https://www.familysearch.org/en/blog/explore-historical-images-tool-unlocks-data-in-digital-records).

FamilySearch's terms of use forbid robots, spiders or other automatic devices on the site, which is why this tool only records what a person read. Public scripts that walk image URLs exist; this project does not use them.

## What it does not do

It does not read the image for you, and it does not know whether the register is sorted by date. Baptisms are usually sorted; a register that mixes years will mislead the interpolation, and the landmarks will show that when the dates stop rising.
