"use client";

import * as React from "react";
import { Check, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command";

/**
 * UNDropdown Component
 * 
 * A custom searchable multi-select dropdown component for UN applications.
 * Features a clean design with search functionality and checkbox-style selection.
 * 
 * @example
 * ```tsx
 * <UNDropdown
 *   placeholder="Select donors..."
 *   options={[
 *     { value: "adb", label: "African Development Bank" },
 *     { value: "au", label: "African Union" }
 *   ]}
 *   value={selectedValues}
 *   onChange={setSelectedValues}
 * />
 * ```
 */

export interface UNDropdownOption {
    value: string;
    label: string;
}

interface UNDropdownProps {
    options: UNDropdownOption[];
    value?: string[];
    onChange?: (value: string[]) => void;
    placeholder?: string;
    searchPlaceholder?: string;
    emptyMessage?: string;
}

export function UNDropdown({
    options,
    value = [],
    onChange,
    placeholder = "Select items...",
    searchPlaceholder = "Search...",
    emptyMessage = "No results found.",
}: UNDropdownProps) {
    const [open, setOpen] = React.useState(false);

    const handleSelect = (selectedValue: string) => {
        const newValue = value.includes(selectedValue)
            ? value.filter((v) => v !== selectedValue)
            : [...value, selectedValue];
        onChange?.(newValue);
    };

    const selectedLabels = value
        .map((v) => options.find((opt) => opt.value === v)?.label)
        .filter(Boolean)
        .join(", ");

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    role="combobox"
                    aria-expanded={open}
                    className="w-full justify-between shadow-none hover:bg-gray-50"
                >
                    <span className="truncate">
                        {selectedLabels || placeholder}
                    </span>
                    <Search className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[400px] p-0 shadow-none" align="start">
                <Command>
                    <CommandInput placeholder={searchPlaceholder} />
                    <CommandList>
                        <CommandEmpty>{emptyMessage}</CommandEmpty>
                        <CommandGroup>
                            {options.map((option) => (
                                <CommandItem
                                    key={option.value}
                                    value={option.value}
                                    onSelect={() => handleSelect(option.value)}
                                >
                                    <Check
                                        className={cn(
                                            "mr-2 h-4 w-4",
                                            value.includes(option.value)
                                                ? "opacity-100"
                                                : "opacity-0"
                                        )}
                                    />
                                    {option.label}
                                </CommandItem>
                            ))}
                        </CommandGroup>
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
    );
}
