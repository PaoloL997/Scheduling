# Excel importer docstrings give the wrong column order

ui/services/excel_importer.py module docstring and `parse_excel` docstring: they give the column order as categoria, uid, misura, note, stabilimento and mark all five as required, but the code reads column 4 as the plant and column 5 as the note (`plant = _str_or_none(cells[3])`, `note = _str_or_none(cells[4])`, which is also what the help text in ui/templates/ui/inventario.html shows) and only checks categoria and uid. Correct the two docstrings to the order and requirements the code applies.
