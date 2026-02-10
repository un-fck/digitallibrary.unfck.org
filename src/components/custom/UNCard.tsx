import * as React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * UNCard Component
 * 
 * A custom card component that extends shadcn/ui Card primitive.
 * Removes the default drop shadow for a cleaner UN design aesthetic.
 * 
 * @example
 * ```tsx
 * <UNCard>
 *   <UNCardHeader>
 *     <UNCardTitle>Title</UNCardTitle>
 *     <UNCardDescription>Description</UNCardDescription>
 *   </UNCardHeader>
 *   <UNCardContent>Content goes here</UNCardContent>
 * </UNCard>
 * ```
 */
const UNCard = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <Card
        ref={ref}
        className={cn("shadow-none", className)}
        {...props}
    />
));
UNCard.displayName = "UNCard";

const UNCardHeader = CardHeader;
const UNCardTitle = CardTitle;
const UNCardDescription = CardDescription;
const UNCardContent = CardContent;

export { UNCard, UNCardHeader, UNCardTitle, UNCardDescription, UNCardContent };
