local source = debug.getinfo(1, "S").source:sub(2)
local extension_dir = source:match("^(.*)[/\\][^/\\]+$")
local manifest_path = extension_dir .. "/generated-manifest.lua"
local manifest = dofile(manifest_path)

local function stringify(value)
  if value == nil then
    return nil
  end
  return pandoc.utils.stringify(value)
end

return {
  ["code-ref"] = function(args, kwargs)
    local reference_id = stringify(args[1])
    if reference_id == nil or reference_id == "" then
      error("code-ref requires a reference id")
    end

    local reference = manifest.references[reference_id]
    if reference == nil then
      error("Unknown code reference id: " .. reference_id)
    end
    local commit = reference.commit
    local symbol = stringify(kwargs.symbol)
    if symbol == "" then
      symbol = nil
    end
    local url = reference.url
    if symbol ~= nil then
      local symbol_reference = reference.symbols[symbol]
      if symbol_reference == nil then
        error("Unknown symbol " .. symbol .. " for code reference " .. reference_id)
      end
      url = symbol_reference.url
    end

    local label = stringify(kwargs.label)
    if label == "" then
      label = nil
    end
    label = label or symbol or reference.path
    local title = reference.path .. " @ " .. commit
    if symbol ~= nil then
      title = symbol .. " in " .. title
    end

    if quarto.doc.isFormat("html") then
      local attr = pandoc.Attr(
        "",
        { "code-reference", "external" },
        {
          { "aria-label", label .. " の検証済みコードをGitHubで開く" },
          { "target", "_blank" },
          { "rel", "noopener" },
        }
      )
      return pandoc.Link(
        { pandoc.Code(label), pandoc.Space(), pandoc.Str("↗") },
        url,
        title,
        attr
      )
    end

    local short_commit = commit:sub(1, 7)
    local short_path = reference.path:match("([^/]+)$") or reference.path
    local context = short_path .. " @ " .. short_commit
    if symbol == nil then
      context = short_commit
    end
    return pandoc.Link(
      {
        pandoc.Code(label),
        pandoc.Space(),
        pandoc.Str("["),
        pandoc.Code(context),
        pandoc.Str("]"),
      },
      url,
      title
    )
  end,
}
