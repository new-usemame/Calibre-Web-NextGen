# Stored rules unavailable in the New UI

A smart shelf can contain a saved field or operator that the current New UI rule schema does not offer. For example, a library custom column may have been removed, or a saved operator may no longer be supported by that field. The editor shows these as read-only **Unsupported rule** rows with the saved field, operator and value.

The warning means the New UI cannot edit that rule. It does not remove the rule or change the server's evaluation of it. If a rule still works in the server or Classic editor, it can still affect membership. Renaming or saving the shelf retains its saved rule values and nested AND/OR groups. JSON numbers, booleans, null and arrays retain their types. Values appear as plain text, including future JSON object values.

Use a row's Remove rule button to remove that rule explicitly. The editor prunes any nested group left empty and preserves the surrounding rules and conditions. Removing a rule can change which books match. If it was the final rule, the editor leaves a visible editable Title rule, following the normal new-rule default, and moves keyboard focus to Add rule. Set the replacement field, operator and value before saving; an empty Title contains value follows the existing broad-match behavior.

A shelf containing saved rules that currently cannot be evaluated returns no matching books, as before, but still supplies those rules to the editor. Opening or renaming that shelf therefore does not replace its saved tree with a blank default rule. A genuinely empty stored tree continues to open with the normal editable default rule. Existing shelf ownership and visibility permissions continue to apply.

The New UI does not offer a new raw rule editor or invent a replacement field/operator. The Classic editor may be used where it supports the stored rule. A temporarily unavailable schema does not justify silently deleting reader data.
