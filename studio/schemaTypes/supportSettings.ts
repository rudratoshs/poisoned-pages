import {defineArrayMember, defineField, defineType} from 'sanity'

// Structured limits the support agent enforces. Only admins edit this document.
// The guard reads these numbers directly, never from article text.
export const supportSettings = defineType({
  name: 'supportSettings',
  title: 'Support settings',
  type: 'document',
  fields: [
    defineField({name: 'title', type: 'string', initialValue: 'Support settings'}),
    defineField({
      name: 'maxAutoRefund',
      title: 'Max refund the agent may issue without a human (USD)',
      type: 'number',
      validation: (r) => r.required().min(0),
    }),
    defineField({name: 'refundWindowDays', type: 'number', validation: (r) => r.required().min(0)}),
    defineField({
      name: 'officialEmailDomains',
      title: 'Official email domains',
      description: 'The agent may only email addresses on these domains unless the customer typed the address.',
      type: 'array',
      of: [defineArrayMember({type: 'string'})],
    }),
  ],
})
