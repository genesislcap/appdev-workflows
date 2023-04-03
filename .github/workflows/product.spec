Name:
Version:        1.0.0
Release:        1%{?dist}
Summary:        genesis-rpm 
BuildArch:      noarch

License:        (c) genesis.global
Group:          Genesis Platform
URL:
Source0:        server-%{version}.tar.gz
Source1:        web-%{version}.tar.gz

BuildRequires:
Requires:       /bin/sh

%description


%prep
%setup -q


%build
%configure
make %{?_smp_mflags}


%install
rm -rf $RPM_BUILD_ROOT
%make_install


%files
%doc



%changelog
