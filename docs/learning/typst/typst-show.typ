#import "@preview/orange-book:0.7.1" as orange

#let part = orange.part
#let chapter = orange.chapter
#let appendices = orange.appendices

#let book(body, ..args) = orange.book(..args)[
  #set page(
    header: context {
      set text(size: 11pt)
      let page-number = counter(page).at(here()).first()
      let chapters = query(heading.where(level: 1))
      let chapter-page = chapters.any(it => it.location().page() == page-number)
      let recto-filler = calc.even(page-number) and chapters.any(
        it => it.location().page() == page-number + 1
      )

      if not chapter-page and not recto-filler {
        let before = query(selector(heading.where(level: 1)).before(here()))
        let chapter-title = if before == () { [] } else { before.last().body }
        box(
          width: 100%,
          inset: (bottom: 5pt),
          stroke: (bottom: 0.5pt),
          align(
            if calc.odd(page-number) { right } else { left },
            chapter-title,
          ),
        )
      }
    },
    footer: context {
      let page-number = counter(page).at(here()).first()
      let chapters = query(heading.where(level: 1))
      let recto-filler = calc.even(page-number) and chapters.any(
        it => it.location().page() == page-number + 1
      )
      if not recto-filler {
        align(center, [#page-number])
      }
    },
  )
  #body
]

#show: book.with(
$if(title)$
  title: [$title$],
$endif$
$if(subtitle)$
  subtitle: [$subtitle$],
$endif$
$if(by-author)$
  author: "$for(by-author)$$it.name.literal$$sep$, $endfor$",
$endif$
$if(date)$
  date: "$date$",
$endif$
$if(lang)$
  lang: "$lang$",
$endif$
  main-color: brand-color.at("primary", default: blue),
  logo: {
    let logo-info = brand-logo.at("medium", default: none)
    if logo-info != none { image(logo-info.path, alt: logo-info.at("alt", default: none)) }
  },
$if(toc-depth)$
  outline-depth: $toc-depth$,
$endif$
  outline-small-depth: 1,
$if(lof)$
$if(crossref.lof-title)$
  list-of-figure-title: "$crossref.lof-title$",
$else$
$if(quarto.language.crossref-lof-title)$
  list-of-figure-title: "$quarto.language.crossref-lof-title$",
$endif$
$endif$
$endif$
$if(lot)$
$if(crossref.lot-title)$
  list-of-table-title: "$crossref.lot-title$",
$else$
$if(quarto.language.crossref-lot-title)$
  list-of-table-title: "$quarto.language.crossref-lot-title$",
$endif$
$endif$
$endif$
$if(quarto.language.crossref-ch-prefix)$
  supplement-chapter: "$quarto.language.crossref-ch-prefix$",
$endif$
$if(margin-geometry)$
  padded-heading-number: false,
$endif$
)

$if(margin-geometry)$
#import "@preview/marginalia:0.3.1" as marginalia

#show: marginalia.setup.with(
  inner: (
    far: $margin-geometry.inner.far$,
    width: $margin-geometry.inner.width$,
    sep: $margin-geometry.inner.separation$,
  ),
  outer: (
    far: $margin-geometry.outer.far$,
    width: $margin-geometry.outer.width$,
    sep: $margin-geometry.outer.separation$,
  ),
  top: $if(margin.top)$$margin.top$$else$1.25in$endif$,
  bottom: $if(margin.bottom)$$margin.bottom$$else$1.25in$endif$,
  book: true,
  clearance: $margin-geometry.clearance$,
)
$endif$
