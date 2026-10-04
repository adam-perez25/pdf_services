import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pypdf import PdfWriter


class PdfMergerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Unir PDF")
        self.root.resizable(False, False)
        self.selected_files = []

        frame = ttk.Frame(root, padding=16)
        frame.grid()

        ttk.Label(frame, text="Archivos PDF seleccionados:").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        self.files_list = tk.Listbox(frame, width=60, height=6)
        self.files_list.grid(row=1, column=0, rowspan=2, pady=(6, 8))

        ttk.Button(frame, text="Subir", command=lambda: self.move_file(-1)).grid(
            row=1, column=1, padx=(8, 0), pady=(6, 2), sticky="ew"
        )
        ttk.Button(frame, text="Bajar", command=lambda: self.move_file(1)).grid(
            row=2, column=1, padx=(8, 0), pady=(2, 8), sticky="ew"
        )

        ttk.Button(frame, text="Seleccionar PDFs", command=self.select_files).grid(
            row=3, column=0, sticky="w"
        )
        ttk.Button(frame, text="Limpiar", command=self.clear_files).grid(
            row=3, column=1, sticky="e"
        )

        ttk.Label(frame, text="Nombre del PDF resultante:").grid(
            row=4, column=0, columnspan=2, pady=(16, 4), sticky="w"
        )
        self.output_name = ttk.Entry(frame, width=60)
        self.output_name.insert(0, "merged.pdf")
        self.output_name.grid(row=5, column=0, columnspan=2)

        ttk.Button(frame, text="Unir PDF", command=self.merge_files).grid(
            row=6, column=0, columnspan=2, pady=(16, 0)
        )

    def select_files(self):
        files = filedialog.askopenfilenames(
            title="Selecciona los archivos PDF que quieras unir",
            filetypes=[("Archivos PDF", "*.pdf"), ("Todos los archivos", "*.*")],
        )

        if files:
            self.selected_files = list(files)
            self.files_list.delete(0, tk.END)
            for file in self.selected_files:
                self.files_list.insert(tk.END, file)

    def clear_files(self):
        self.selected_files = []
        self.files_list.delete(0, tk.END)

    def move_file(self, offset):
        selection = self.files_list.curselection()
        if not selection:
            return

        current_index = selection[0]
        new_index = current_index + offset
        if new_index < 0 or new_index >= len(self.selected_files):
            return

        self.selected_files[current_index], self.selected_files[new_index] = (
            self.selected_files[new_index],
            self.selected_files[current_index],
        )
        self.files_list.delete(0, tk.END)
        for file in self.selected_files:
            self.files_list.insert(tk.END, file)
        self.files_list.selection_set(new_index)
        self.files_list.activate(new_index)

    def merge_files(self):
        if not self.selected_files:
            messagebox.showerror("Faltan archivos", "Selecciona al menos un archivo PDF.")
            return

        output_name = self.output_name.get().strip()
        if not output_name:
            messagebox.showerror("Nombre inválido", "Escribe un nombre para el PDF resultante.")
            return

        if not output_name.lower().endswith(".pdf"):
            output_name += ".pdf"

        output_file = filedialog.asksaveasfilename(
            title="Guardar PDF resultante",
            initialfile=output_name,
            defaultextension=".pdf",
            filetypes=[("Archivos PDF", "*.pdf")],
        )
        if not output_file:
            return

        try:
            writer = PdfWriter()
            for file in self.selected_files:
                writer.append(file)
            writer.write(output_file)
            writer.close()
        except Exception as error:
            messagebox.showerror("Error", f"No se pudo unir los archivos:\n{error}")
            return

        self.clear_files()
        self.output_name.delete(0, tk.END)
        self.output_name.insert(0, "merged.pdf")
        messagebox.showinfo(
            "PDF creado", f"El PDF se guardó en:\n{Path(output_file)}"
        )


root = tk.Tk()
PdfMergerApp(root)
root.mainloop()