package.path = "../?.lua;" .. package.path

local Delivery = require("delivery")
local Actions = require("device_actions")
local Collections = require("device_collections")

local downloads = 0
local removed = {}
local installed, reason = Delivery.install({
    id = 1, filename = "Large.epub", size = 2 * 1024 * 1024,
}, "/library", {
    attributes = function() return nil end,
    digest = function() return nil end,
    sanitize = function(name) return name end,
    remove = function(path) removed[#removed + 1] = path end,
    persist_receipt = function() return true end,
    rename = function() return true end,
    available_space = function() return 1024 end,
    download = function()
        downloads = downloads + 1
        return true
    end,
})
assert(installed == nil, "a book larger than free space must not install")
assert(reason == "insufficient storage",
    "the refusal must name insufficient storage, got: " .. tostring(reason))
assert(downloads == 0, "space refusal must happen before opening the download")
assert(#removed == 0, "space refusal must not create or clean a partial file")

local files = { ["/library/Books/Named.epub"] = true }
local deleted, delete_reason = Actions.deleteNamed({
    lpath = "Books/Named.epub",
    checksum = "0123456789abcdef0123456789abcdef",
}, "/library", {
    attributes = function(path) return files[path] and { mode = "file" } or nil end,
    digest = function() return "0123456789abcdef0123456789abcdef" end,
    remove = function(path) files[path] = nil return true end,
})
assert(deleted and delete_reason == nil,
    "a named delete of a matching file must succeed: " .. tostring(delete_reason))
assert(files["/library/Books/Named.epub"] == nil,
    "a confirmed named delete must actually remove the file")

files["/library/Books/Replacement.epub"] = true
local mismatch, mismatch_reason = Actions.deleteNamed({
    lpath = "Books/Replacement.epub",
    checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}, "/library", {
    attributes = function(path) return files[path] and { mode = "file" } or nil end,
    digest = function() return "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" end,
    remove = function(path) files[path] = nil return true end,
})
assert(not mismatch and mismatch_reason == "checksum mismatch",
    "a file whose digest does not match the named one must NOT be deleted: "
    .. tostring(mismatch_reason))
assert(files["/library/Books/Replacement.epub"],
    "the refused delete must leave the mismatching file in place")

local escaped = Actions.deleteNamed({
    lpath = "../Outside.epub", checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}, "/library", {
    attributes = function() return { mode = "file" } end,
    digest = function() return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" end,
    remove = function() error("escaped delete") end,
})
assert(not escaped,
    "a path escaping the library root must be refused before any remove() call")

local fake = { collections = {} }
function fake:addCollection(name) self.collections[name] = self.collections[name] or {} end
function fake:removeCollection(name) self.collections[name] = nil end
function fake:addItem(path, name) self.collections[name][path] = true end
function fake:write() return true end
local state = {}
Collections.apply({ scope = "account-one", revision = 1, collections = {
    { id = "shelf-1", name = "Reading", books = { "First.epub" } },
}}, "/library", fake, state)
Collections.apply({ scope = "account-two", revision = 1, collections = {
    { id = "shelf-2", name = "Reading", books = { "Second.epub" } },
}}, "/library", fake, state)

local first_name = state["account-one"].names["shelf-1"]
local second_name = state["account-two"].names["shelf-2"]
assert(first_name ~= second_name, "account scopes must not share collection names")
assert(fake.collections[first_name]["/library/First.epub"],
    "a shelf's book must land in that shelf's managed collection")
assert(fake.collections[second_name]["/library/Second.epub"],
    "a second account's collection must be built independently")

Collections.apply({ scope = "account-one", revision = 2, collections = {
    { id = "shelf-1", name = "Reading", books = { "Replacement.epub" } },
}}, "/library", fake, state)
assert(not fake.collections[first_name]["/library/First.epub"],
    "a book removed from a shelf must leave its managed collection")
assert(fake.collections[first_name]["/library/Replacement.epub"],
    "a refreshed shelf must contain its new membership")

Collections.apply({ scope = "account-one", revision = 3, collections = {
    { id = "shelf-1", name = "Finished", books = { "Replacement.epub" } },
}}, "/library", fake, state)
local renamed = state["account-one"].names["shelf-1"]
assert(renamed ~= first_name, "renaming a shelf must rename its managed collection")
assert(fake.collections[first_name] == nil,
    "renaming a shelf must remove the collection under its old name")
assert(fake.collections[renamed]["/library/Replacement.epub"],
    "the renamed collection must carry the membership over")

Collections.apply({ scope = "account-one", revision = 4, collections = {} },
    "/library", fake, state)
assert(fake.collections[renamed] == nil,
    "a shelf removed on the server must remove its managed collection")
assert(fake.collections[second_name]["/library/Second.epub"],
    "one account's refresh must not remove another account's collections")

print("device_capabilities_test.lua: 1 suite passed")
