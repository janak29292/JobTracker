from urllib.parse import urlparse, parse_qs

from rest_framework.pagination import CursorPagination
from rest_framework.response import Response


class CustomCursorPagination(CursorPagination):
    ordering = 'id'
    cursor_query_param = 'cursor'

    def get_paginated_response(self, data):
        next_link = self.get_next_link()
        previous_link = self.get_previous_link()
        next_param_object, next_params = self.extract_cursor_param(next_link)
        previous_param_object, previous_params = self.extract_cursor_param(previous_link)

        return Response({
            'next': next_link,
            'previous': previous_link,
            'next_params': next_params,
            'previous_params': previous_params,
            'page_size': self.get_page_size(self.request),
            'next_param_object': next_param_object,
            'previous_param_object': previous_param_object,
            # 'count': self.page.paginator.count,
            'results': data
        })

    def extract_cursor_param(self, url):
        """
        Helper method to extract the cursor parameter value from a full URL.
        """
        if url is None:
            return None, None
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        # Return the value of the cursor_query_param
        # return query_params.get(self.cursor_query_param, [None])[0]
        return query_params, parsed_url.query
