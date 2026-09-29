import { apiFetch } from './client';

export interface CalendarSubject {
  id: number;
  name: string;
  original_name: string;
  poster_url: string;
  rating: number;
  air_date: string;
}

export interface CalendarDay {
  weekday: number;
  items: CalendarSubject[];
}

export const getCalendar = () => apiFetch<CalendarDay[]>('/api/explore/calendar');
