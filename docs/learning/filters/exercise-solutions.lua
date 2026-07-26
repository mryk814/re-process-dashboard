local collected_solutions = {}
local solution_placement = nil
local current_solution_group = nil

local function read_solution_placement(metadata)
  local configured = metadata["solution-placement"]
  if configured == nil then
    solution_placement = nil
  else
    solution_placement = pandoc.utils.stringify(configured)
  end
end

local function has_class(classes, expected)
  for _, class_name in ipairs(classes) do
    if class_name == expected then
      return true
    end
  end
  return false
end

local function solution_label(element)
  local label = element.attributes["data-label"]
  if label == nil or label == "" then
    return "解答例"
  end
  return label
end

local function remember_chapter(element)
  if element.level == 1 then
    current_solution_group = pandoc.utils.stringify(element.content)
  end
  return nil
end

local function transform_solution(element)
  if not has_class(element.classes, "exercise-solution") then
    return nil
  end

  if element.identifier == "" then
    error("exercise-solution requires an identifier")
  end

  if quarto.doc.is_format("html") then
    if solution_placement ~= "inline-disclosure" then
      error(
        "HTML exercise solutions require solution-placement: inline-disclosure"
      )
    end
    local identifier = element.identifier
    local label = solution_label(element)
    element.identifier = ""
    element.attributes["data-label"] = nil

    return {
      pandoc.RawBlock(
        "html",
        '<details id="' .. identifier
          .. '" class="exercise-solution" data-answer-content>'
          .. '<summary>解答例を見る</summary>'
      ),
      pandoc.Div(element.content, pandoc.Attr("", {"exercise-solution__body"})),
      pandoc.RawBlock("html", "</details>"),
    }
  end

  if quarto.doc.is_format("typst") then
    if solution_placement ~= "answer-chapter" then
      error(
        "Typst exercise solutions require solution-placement: answer-chapter"
      )
    end
    if current_solution_group == nil or current_solution_group == "" then
      error(
        "Typst exercise solutions require a preceding level-1 chapter heading"
      )
    end
    table.insert(
      collected_solutions,
      {
        block = element,
        group = current_solution_group,
      }
    )
    return {}
  end

  return element
end

local function insert_solution_chapter(document)
  if #collected_solutions == 0 then
    return document
  end

  if not quarto.doc.is_format("typst") then
    error("exercise solutions were collected for a non-Typst format")
  end

  local solution_blocks = pandoc.Blocks({
    pandoc.RawBlock("typst", "#pagebreak(weak: true)"),
    pandoc.Header(
      1,
      pandoc.Inlines({pandoc.Str("演習解答")}),
      pandoc.Attr("sec-exercise-solutions")
    ),
  })

  local previous_group = nil
  for _, collected in ipairs(collected_solutions) do
    local solution = collected.block
    if collected.group ~= previous_group then
      if previous_group ~= nil then
        solution_blocks:insert(
          pandoc.RawBlock("typst", "#pagebreak(weak: true)")
        )
      end
      solution_blocks:insert(
        pandoc.Header(
          2,
          pandoc.Inlines({pandoc.Str(collected.group)})
        )
      )
      previous_group = collected.group
    end

    local label = solution_label(solution)
    local identifier = solution.identifier
    solution.identifier = ""
    solution.attributes["data-label"] = nil

    solution_blocks:insert(
      pandoc.Header(
        3,
        pandoc.Inlines({pandoc.Str(label)}),
        pandoc.Attr(identifier)
      )
    )
    for _, block in ipairs(solution.content) do
      solution_blocks:insert(block)
    end
  end

  local output_blocks = pandoc.Blocks({})
  local inserted = false
  for _, block in ipairs(document.blocks) do
    if not inserted
      and block.t == "Header"
      and block.level == 1
      and block.identifier == "sec-glossary"
    then
      for _, solution_block in ipairs(solution_blocks) do
        output_blocks:insert(solution_block)
      end
      inserted = true
    end
    output_blocks:insert(block)
  end

  if not inserted then
    for _, solution_block in ipairs(solution_blocks) do
      output_blocks:insert(solution_block)
    end
  end

  document.blocks = output_blocks
  collected_solutions = {}
  current_solution_group = nil
  return document
end

return {
  {
    Meta = read_solution_placement,
  },
  {
    Header = remember_chapter,
    Div = transform_solution,
    Pandoc = insert_solution_chapter,
  },
}
